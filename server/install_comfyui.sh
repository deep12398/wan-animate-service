#!/usr/bin/env bash
# 宿主机直装 ComfyUI + Wan2.2-Animate 所需节点（venv 方式，便于逐步调试）
# 用法: nohup bash install_comfyui.sh > /data/install.log 2>&1 &
set -uo pipefail
TS() { date "+%H:%M:%S"; }

COMFY=/root/ComfyUI
VENV=/root/comfyui-venv
export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

echo "[$(TS)] === 1/6 系统依赖 ==="
apt-get update -qq
apt-get install -y -qq ffmpeg python3-venv python3-dev build-essential libgl1 libglib2.0-0 >/dev/null

echo "[$(TS)] === 2/6 venv + PyTorch 2.8 cu128 ==="
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q -U pip
# 走阿里云 PyTorch 镜像(同机房,极快)；官方 download.pytorch.org 从国内超时
# 三件套一次钉死(含 torchaudio!)：否则后面 ComfyUI requirements 里没钉版本的
# torchaudio 会触发 pip 把 torch 连带升到最新，冲掉这里的版本。
"$VENV/bin/pip" install -q \
    "torch==2.8.0+cu128" "torchvision==0.23.0+cu128" "torchaudio==2.8.0+cu128" \
    --find-links https://mirrors.aliyun.com/pytorch-wheels/cu128/ \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple

echo "[$(TS)] === 3/6 ComfyUI 主体 ==="
[ -d "$COMFY" ] || git clone -q https://github.com/comfyanonymous/ComfyUI "$COMFY"
"$VENV/bin/pip" install -q -r "$COMFY/requirements.txt"

echo "[$(TS)] === 4/6 自定义节点 ==="
cd "$COMFY/custom_nodes"
for repo in \
    kijai/ComfyUI-WanVideoWrapper \
    kijai/ComfyUI-KJNodes \
    kijai/ComfyUI-segment-anything-2 \
    Kosinkadink/ComfyUI-VideoHelperSuite \
    Fannovel16/comfyui_controlnet_aux ; do
  name=$(basename "$repo")
  [ -d "$name" ] || git clone -q --depth 1 "https://github.com/$repo.git"
  echo "[$(TS)]   cloned $name"
done

echo "[$(TS)] === 5/6 节点依赖 ==="
for d in ComfyUI-WanVideoWrapper ComfyUI-KJNodes ComfyUI-segment-anything-2 \
         ComfyUI-VideoHelperSuite comfyui_controlnet_aux ; do
  if [ -f "$d/requirements.txt" ]; then
    "$VENV/bin/pip" install -q -r "$d/requirements.txt" && echo "[$(TS)]   deps ok: $d" || echo "[$(TS)]   deps WARN: $d"
  fi
done
"$VENV/bin/pip" install -q onnxruntime-gpu || echo "[$(TS)]   onnxruntime-gpu WARN"

echo "[$(TS)] === 6/6 模型软链接 ==="
M=/data/comfyui-models
for sub in diffusion_models text_encoders loras vae clip_vision; do
  mkdir -p "$COMFY/models/$sub"
  # 把已下载的模型软链进 ComfyUI（避免重复占盘）
  if [ -d "$M/$sub" ]; then
    for f in "$M/$sub"/*; do
      [ -e "$f" ] && ln -sf "$f" "$COMFY/models/$sub/$(basename "$f")"
    done
  fi
done
mkdir -p /data/comfyui-input /data/comfyui-output
ln -sfn /data/comfyui-input "$COMFY/input"
ln -sfn /data/comfyui-output "$COMFY/output"

echo "[$(TS)] === 安装完成 ==="
"$VENV/bin/python" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
touch /data/.comfyui_installed
echo "[$(TS)] INSTALL_DONE"
