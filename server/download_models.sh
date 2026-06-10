#!/usr/bin/env bash
# Wan2.2-Animate 模型下载脚本（全部 URL 已于 2026-06-09 在 hf-mirror.com 实测验证）
# 用法: nohup bash download_models.sh > /data/download.log 2>&1 &
set -uo pipefail

MODELS_ROOT=/data/comfyui-models
MIRROR=https://hf-mirror.com
LOG_TS() { date "+%H:%M:%S"; }

mkdir -p "$MODELS_ROOT"/{diffusion_models,text_encoders,loras,vae,clip_vision}

# 格式: "目标子目录|目标文件名|远程相对路径"
MODELS=(
  "diffusion_models|Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors|Kijai/WanVideo_comfy_fp8_scaled/resolve/main/Wan22Animate/Wan2_2-Animate-14B_fp8_scaled_e4m3fn_KJ_v2.safetensors"
  "text_encoders|umt5-xxl-enc-fp8_e4m3fn.safetensors|Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-fp8_e4m3fn.safetensors"
  "vae|Wan2_1_VAE_bf16.safetensors|Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors"
  "clip_vision|clip_vision_h.safetensors|Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors"
  "loras|WanAnimate_relight_lora_fp16.safetensors|Kijai/WanVideo_comfy/resolve/main/LoRAs/Wan22_relight/WanAnimate_relight_lora_fp16.safetensors"
  "loras|lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors|Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors"
)

echo "[$(LOG_TS)] ===== 开始下载 ${#MODELS[@]} 个模型到 $MODELS_ROOT ====="
FAIL=0
for entry in "${MODELS[@]}"; do
  IFS='|' read -r subdir fname relpath <<< "$entry"
  dest="$MODELS_ROOT/$subdir/$fname"
  url="$MIRROR/$relpath"
  echo "[$(LOG_TS)] >>> $subdir/$fname"
  # -c 断点续传, --tries 重试, 超时保护
  wget -c --tries=5 --timeout=60 --waitretry=10 -q --show-progress \
    -O "$dest" "$url"
  if [[ $? -ne 0 ]]; then
    echo "[$(LOG_TS)] !!! 下载失败: $fname"
    FAIL=1
  else
    sz=$(du -h "$dest" | cut -f1)
    echo "[$(LOG_TS)] OK  $fname  ($sz)"
  fi
done

echo "[$(LOG_TS)] ===== 下载完成, 文件清单 ====="
find "$MODELS_ROOT" -type f \( -name "*.safetensors" -o -name "*.pt" \) -exec du -h {} \;

if [[ $FAIL -eq 0 ]]; then
  echo "[$(LOG_TS)] ALL_DONE_OK"
  touch "$MODELS_ROOT/.download_complete"
else
  echo "[$(LOG_TS)] DONE_WITH_ERRORS"
fi
