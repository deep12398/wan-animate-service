#!/usr/bin/env python3
"""把 ComfyUI 的 UI 格式工作流忠实转成 API 格式（/prompt 接口要的扁平字典）。

复刻前端 graphToPrompt 的核心逻辑：
  1. 跳过纯虚拟节点(Reroute/SetNode/GetNode/Note/...)，把数据流"内联"穿过它们；
  2. 真实节点 → {class_type, inputs}；链接输入填 [src_id, src_slot]，widget 输入按
     object_info 里的顺序从 widgets_values 取值；
  3. 可选剥离 replace 模式分支(SAM2/PointsEditor)，只留 animate 主链。

用法:
  python convert_ui_to_api.py <ui.json> <object_info.json> <out_api.json> [--drop-replace]
"""
from __future__ import annotations

import json
import sys

VIRTUAL = {"Reroute", "SetNode", "GetNode", "Note", "MarkdownNote", "PrimitiveNode"}
# 纯 replace 模式 / 交互节点：animate-only 时剥离。
# 注意：FaceMaskFromPoseKeypoints / ImageCropByMaskAndResize 不在此列——
# 它们产出 animate 必需的 face_images，必须保留。
REPLACE_ONLY = {
    "Sam2Segmentation", "DownloadAndLoadSAM2Model", "PointsEditor",
    "DrawMaskOnImage", "BlockifyMask", "GrowMask",
}


def load(ui_path, oinfo_path):
    ui = json.load(open(ui_path, encoding="utf-8"))
    oinfo = json.load(open(oinfo_path, encoding="utf-8"))
    nodes = {n["id"]: n for n in ui["nodes"]}
    # links: [id, src_node, src_slot, dst_node, dst_slot, type]
    links = {l[0]: l for l in ui.get("links", [])}
    return ui, oinfo, nodes, links


def input_link(nodes, links, node_id, slot_index):
    """返回连到 (node_id, 输入槽 slot_index) 的 link，没有则 None。"""
    node = nodes[node_id]
    inputs = node.get("inputs", [])
    if slot_index >= len(inputs):
        return None
    lid = inputs[slot_index].get("link")
    return links.get(lid) if lid is not None else None


def set_source_by_name(nodes, links, name):
    """找到 SetNode(name=name) 的输入来源 link。"""
    for n in nodes.values():
        if n["type"] == "SetNode" and (n.get("widgets_values") or [None])[0] == name:
            return input_link(nodes, links, n["id"], 0)
    return None


def resolve(nodes, links, node_id, slot):
    """把 (node_id, 输出槽) 穿过虚拟节点，解析到真实的 (node_id, slot)。"""
    seen = set()
    while True:
        if node_id in seen:
            raise ValueError(f"虚拟节点成环: {node_id}")
        seen.add(node_id)
        node = nodes.get(node_id)
        if node is None:
            return None
        t = node["type"]
        if t == "Reroute":
            l = input_link(nodes, links, node_id, 0)
            if not l:
                return None
            node_id, slot = l[1], l[2]
        elif t == "GetNode":
            name = (node.get("widgets_values") or [None])[0]
            l = set_source_by_name(nodes, links, name)
            if not l:
                return None
            node_id, slot = l[1], l[2]
        elif t == "SetNode":
            l = input_link(nodes, links, node_id, 0)
            if not l:
                return None
            node_id, slot = l[1], l[2]
        else:
            return node_id, slot


def widget_input_names(oinfo, class_type):
    """从 object_info 取该节点 widget 输入的有序名字（用于把 widgets_values 对上号）。

    约定：required + optional 里，值是 [类型字符串或列表] 的算 widget（有 UI 控件），
    值是连接型(其类型是另一个节点输出，如 'IMAGE','MODEL')的算链接输入，不吃 widget 值。
    """
    spec = oinfo.get(class_type, {}).get("input", {})
    names = []
    for group in ("required", "optional"):
        for name, meta in spec.get(group, {}).items():
            t = meta[0] if isinstance(meta, list) and meta else meta
            # 列表 = 下拉枚举(widget)；'INT'/'FLOAT'/'STRING'/'BOOLEAN' = widget；其余大写类型 = 链接
            is_widget = isinstance(t, list) or t in ("INT", "FLOAT", "STRING", "BOOLEAN")
            if is_widget:
                names.append(name)
    return names


def convert(ui, oinfo, nodes, links, drop_replace=False, drop_ids=None, rewire=None):
    drop_ids = set(drop_ids or [])
    rewire = rewire or {}
    skip_types = set(VIRTUAL)
    if drop_replace:
        skip_types |= REPLACE_ONLY

    # 先算出"保留下来的真实节点 id"集合：任何指向非保留节点的输入都要省略
    kept_ids = {
        str(n["id"]) for n in ui["nodes"]
        if n["type"] not in skip_types and str(n["id"]) not in drop_ids
    }

    api = {}
    for node in ui["nodes"]:
        t = node["type"]
        nid = str(node["id"])
        if t in skip_types or nid in drop_ids:
            continue
        entry = {"class_type": t, "inputs": {}}

        # 1) 链接输入
        for inp in node.get("inputs", []):
            lid = inp.get("link")
            if lid is None:
                continue
            l = links.get(lid)
            if not l:
                continue
            r = resolve(nodes, links, l[1], l[2])
            # 来源被剥离(mask/bg 来自 SAM2，或预览节点)：留空，靠 optional 默认值
            if r is None or str(r[0]) not in kept_ids:
                continue
            entry["inputs"][inp["name"]] = [str(r[0]), r[1]]

        # 2) widget 输入
        wnames = widget_input_names(oinfo, t)
        wvals = node.get("widgets_values", [])
        if isinstance(wvals, dict):
            # VHS 等节点 widgets_values 是 dict，直接按名取
            for k, v in wvals.items():
                if k in wnames:
                    entry["inputs"][k] = v
        else:
            # 按序消费 widgets_values；seed/noise_seed 后面 ComfyUI 多塞了一个
            # control_after_generate 隐藏值，必须跳过一位，否则后续全部错位。
            vi = 0
            for name in wnames:
                if vi >= len(wvals):
                    break
                entry["inputs"].setdefault(name, wvals[vi])  # 已是链接的不覆盖
                vi += 1
                if name in ("seed", "noise_seed"):
                    vi += 1

        api[nid] = entry

    # 3) 改线：把某节点的某输入强制指向另一节点（如把输出 VideoCombine 从"拼接预览"直连"解码结果"）
    for (tgt_id, inp_name), (src_id, src_slot) in rewire.items():
        if tgt_id in api:
            api[tgt_id]["inputs"][inp_name] = [src_id, src_slot]

    return api


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    ui_path, oinfo_path, out_path = sys.argv[1:4]
    drop = "--drop-replace" in sys.argv
    drop_ids = []
    rewire = {}
    for a in sys.argv:
        if a.startswith("--drop-ids="):
            drop_ids = [x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()]
        if a.startswith("--rewire="):
            spec = a.split("=", 1)[1]            # 形如 30:images=42:0
            lhs, rhs = spec.split("=", 1)
            tgt, inp = lhs.split(":")
            src, slot = rhs.split(":")
            rewire[(tgt, inp)] = (src, int(slot))
    ui, oinfo, nodes, links = load(ui_path, oinfo_path)
    api = convert(ui, oinfo, nodes, links, drop_replace=drop, drop_ids=drop_ids, rewire=rewire)
    json.dump(api, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"转换完成: {len(api)} 个节点 -> {out_path}  (drop_replace={drop})")
    # 自检：列出仍引用了不存在节点的输入
    ids = set(api)
    dangling = []
    for nid, e in api.items():
        for name, v in e["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] not in ids:
                dangling.append(f"  #{nid}.{name} -> #{v[0]}(不存在)")
    if dangling:
        print("⚠️ 悬空引用(可能需要保留某些被剥离的节点):")
        print("\n".join(dangling))
    else:
        print("✓ 无悬空引用")


if __name__ == "__main__":
    main()
