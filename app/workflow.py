"""把运行时参数注入 API 格式工作流模板。

设计原则（对应部署文档 8.4 的坑）：绝不重写整个 JSON 结构，只按 class_type 定位节点、
改 inputs 里的少数字段。模板本身是从跑通的 ComfyUI 里 "Save (API Format)" 导出的，保持原样。
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from .config import settings


# 输出比例 -> (width, height)。均可被 16 整除（满足 ImageResizeKJv2.divisible_by=16）。
# 480 宽档：与当前 ~5-6 分钟/条耗时、显存占用基本一致。
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "9:16": (480, 848),   # 竖屏短视频主流比例（默认）
    "3:4":  (480, 640),   # 传统竖图比例
    "1:1":  (640, 640),   # 方形（兼容旧行为）
}


@dataclass
class GenParams:
    image_filename: str          # 已落在 comfy_input 目录里的头像文件名
    video_filename: str          # 已落在 comfy_input 目录里的驱动视频文件名
    prompt: str
    aspect_ratio: str            # 输出比例枚举，见 ASPECT_RATIOS
    frames: int
    seed: int
    steps: int
    # 高级精确覆盖：两者都 >0 时压过 aspect_ratio；默认 0 = 未设置。
    width: int = 0
    height: int = 0

    def resolve_resolution(self) -> tuple[int, int]:
        """求最终 (width, height)：显式 width/height 都 >0 则覆盖，否则用 aspect_ratio 预设。"""
        if self.width > 0 and self.height > 0:
            return self.width, self.height
        if self.aspect_ratio not in ASPECT_RATIOS:
            raise ValueError(
                f"未知 aspect_ratio={self.aspect_ratio!r}，允许值: {', '.join(ASPECT_RATIOS)}"
            )
        return ASPECT_RATIOS[self.aspect_ratio]


# class_type -> 要改的 inputs 字段（运行时按请求填）。
# 分辨率不在这里注入——它由 INTConstant 链接驱动，需顺着链接反查节点，见 _inject_resolution。
_INJECTORS = {
    "LoadImage":               lambda n, p: n["inputs"].update(image=p.image_filename),
    "VHS_LoadVideo":           lambda n, p: n["inputs"].update(video=p.video_filename),
    "WanVideoTextEncodeCached": lambda n, p: n["inputs"].update(positive_prompt=p.prompt),
    "WanVideoSampler":         lambda n, p: n["inputs"].update(seed=p.seed, steps=p.steps),
}


def _resolution_const_ids(wf: dict) -> tuple[str | None, str | None]:
    """顺着 ImageResizeKJv2 的 width/height 链接，反查出驱动分辨率的两个节点 id。

    不依赖被转换器丢掉的 _meta.title，也不写死 150/151——只信赖"谁连到 width/height 输入"。
    返回 (width_src_id, height_src_id)，字面量（非链接）时对应位置为 None。
    """
    for node in wf.values():
        if node.get("class_type") in ("ImageResizeKJv2", "WanVideoAnimateEmbeds"):
            ins = node.get("inputs", {})
            w, h = ins.get("width"), ins.get("height")
            wid = w[0] if isinstance(w, list) and w else None
            hid = h[0] if isinstance(h, list) and h else None
            if wid or hid:
                return wid, hid
    return None, None


def _inject_resolution(wf: dict, width: int, height: int) -> None:
    """把分辨率写进工作流：优先改 INTConstant 源节点，并对字面量形式的消费端做兜底覆盖。"""
    wid, hid = _resolution_const_ids(wf)
    patched = False
    if wid and wf.get(wid, {}).get("class_type") == "INTConstant":
        wf[wid]["inputs"]["value"] = width
        patched = True
    if hid and wf.get(hid, {}).get("class_type") == "INTConstant":
        wf[hid]["inputs"]["value"] = height
        patched = True
    # 兜底：若某些消费端 width/height 是字面量（没走 INTConstant 链接），直接覆盖。
    for node in wf.values():
        if node.get("class_type") in ("ImageResizeKJv2", "WanVideoAnimateEmbeds"):
            ins = node.get("inputs", {})
            if isinstance(ins.get("width"), int):
                ins["width"] = width
                patched = True
            if isinstance(ins.get("height"), int):
                ins["height"] = height
                patched = True
    if not patched:
        raise ValueError("工作流里找不到可注入分辨率的节点（INTConstant/ImageResize/AnimateEmbeds）")


def primary_output_node_id(wf: dict) -> str | None:
    """选出"主输出"VHS_VideoCombine 节点 id。

    工作流里可能有多个 VideoCombine（主成片 + 人脸裁剪预览 + 背景预览）。主成片节点的特征：
    含 audio 输入（唯一）；退而求其次看 filename_prefix 含 'wanimate'；再退则取第一个。
    """
    combines = [
        (nid, node.get("inputs", {}))
        for nid, node in wf.items()
        if node.get("class_type") == "VHS_VideoCombine"
    ]
    for nid, ins in combines:
        if isinstance(ins.get("audio"), list):
            return nid
    for nid, ins in combines:
        if "wanimate" in str(ins.get("filename_prefix", "")).lower():
            return nid
    return combines[0][0] if combines else None


class WorkflowTemplate:
    def __init__(self, path: str | None = None):
        self.path = path or settings.workflow_path
        with open(self.path, "r", encoding="utf-8") as f:
            self._template: dict = json.load(f)

    def build(self, params: GenParams) -> dict:
        """返回一份注入好参数的工作流（深拷贝，不污染模板）。"""
        wf = copy.deepcopy(self._template)
        applied = {ct: False for ct in _INJECTORS}
        for node in wf.values():
            ct = node.get("class_type")
            inj = _INJECTORS.get(ct)
            if inj:
                inj(node, params)
                applied[ct] = True
        missing = [ct for ct, ok in applied.items() if not ok]
        if missing:
            # 模板里少了应有的注入点，宁可早失败也不要跑出错结果
            raise ValueError(f"工作流模板缺少节点类型，无法注入参数: {missing}")
        # 分辨率：按 aspect_ratio（或显式 width/height）注入到驱动节点
        width, height = params.resolve_resolution()
        _inject_resolution(wf, width, height)
        return wf

    @staticmethod
    def primary_output_node_id(wf: dict) -> str | None:
        """主输出 VHS_VideoCombine 节点 id（供客户端优先选取产物）。"""
        return primary_output_node_id(wf)
