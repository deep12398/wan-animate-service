#!/usr/bin/env python3
"""对转换出的 API 工作流做静态值修正：模型文件名(扁平)、A10 专属 attention、
fp8 文本编码器、rank128 加速 lora、默认测试素材。

这些是"值"层面的修正，与节点连接无关，按 class_type 定位后直接覆盖 inputs。
用法: python finalize_workflow.py <workflow_api.json>  (原地修改)
"""
import json
import sys

# class_type -> {输入名: 正确值}
OVERRIDES = {
    "WanVideoModelLoader": {
        # 模型软链接是扁平放在 diffusion_models/ 下，去掉 WanVideo\2_2\ 前缀
        "model": "Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors",
        "attention_mode": "sdpa",          # A10(sm_86) 必须；sageattn 会 no kernel image 崩
    },
    "WanVideoVAELoader": {
        "model_name": "Wan2_1_VAE_bf16.safetensors",
    },
    "WanVideoTextEncodeCached": {
        "model_name": "umt5-xxl-enc-fp8_e4m3fn.safetensors",   # 下的是 fp8 版,省显存
    },
    "CLIPVisionLoader": {
        "clip_name": "clip_vision_h.safetensors",
    },
    "WanVideoLoraSelectMulti": {
        "lora_0": "WanAnimate_relight_lora_fp16.safetensors",
        "lora_1": "lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors",
    },
    # --- 提速配置(A10 显存 24G 有余量) ---
    "WanVideoBlockSwap": {
        # 20：在提速和安全间折中。blockswap=10 实测峰值 22.4G/23G 贴边 OOM，
        # 而原始需求要支持 720p/几十秒(显存更高)，故留足余量。
        "blocks_to_swap": 20,
    },
    "WanVideoSampler": {
        "steps": 4,             # lightx2v 蒸馏支持 4 步(从 6 步降)
    },
    # DWPose 改用 ONNX 模型(onnxruntime-gpu 跑,已本地预置在 ckpts/yzd-v/DWPose/)。
    # 默认的 torchscript 变体路径不同且需联网下载，hf-mirror 故障时会卡死。
    "DWPreprocessor": {
        "bbox_detector": "yolox_l.onnx",
        "pose_estimator": "dw-ll_ucoco_384.onnx",
    },
    # 默认测试素材(零版权)；上线后由 FastAPI 在运行时按请求覆盖
    "LoadImage": {
        "image": "default_image.jpg",
    },
    "VHS_LoadVideo": {
        "video": "default_video.mp4",
    },
    # 产物存到 output 目录持久化(默认 save_output=false 会进 temp，重启即清)
    "VHS_VideoCombine": {
        "save_output": True,
    },
}


def main():
    path = sys.argv[1]
    api = json.load(open(path, encoding="utf-8"))
    applied = []
    for nid, node in api.items():
        ct = node.get("class_type")
        if ct in OVERRIDES:
            for k, v in OVERRIDES[ct].items():
                if k in node["inputs"] and isinstance(node["inputs"][k], list):
                    # 该输入已是链接(连到别的节点)，不要用静态值覆盖
                    continue
                node["inputs"][k] = v
                applied.append(f"#{nid} {ct}.{k} = {v}")
    json.dump(api, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"已修正 {len(applied)} 处:")
    for a in applied:
        print("  " + a)


if __name__ == "__main__":
    main()
