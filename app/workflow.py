"""把运行时参数注入 API 格式工作流模板。

设计原则（对应部署文档 8.4 的坑）：绝不重写整个 JSON 结构，只按 class_type 定位节点、
改 inputs 里的少数字段。模板本身是从跑通的 ComfyUI 里 "Save (API Format)" 导出的，保持原样。
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from .config import settings


@dataclass
class GenParams:
    image_filename: str          # 已落在 comfy_input 目录里的头像文件名
    video_filename: str          # 已落在 comfy_input 目录里的驱动视频文件名
    prompt: str
    width: int
    height: int
    frames: int
    seed: int
    steps: int


# class_type -> 要改的 inputs 字段（运行时按请求填）。
# 注意：分辨率/帧数(WanVideoAnimateEmbeds 的 width/height/num_frames)在工作流里是
# 由 INTConstant 链接驱动的，且和视频加载器联动，不在这里注入，统一用工作流默认 832x480。
_INJECTORS = {
    "LoadImage":               lambda n, p: n["inputs"].update(image=p.image_filename),
    "VHS_LoadVideo":           lambda n, p: n["inputs"].update(video=p.video_filename),
    "WanVideoTextEncodeCached": lambda n, p: n["inputs"].update(positive_prompt=p.prompt),
    "WanVideoSampler":         lambda n, p: n["inputs"].update(seed=p.seed, steps=p.steps),
}


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
        return wf
