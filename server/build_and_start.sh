#!/usr/bin/env bash
# 下载已完成后：装 ComfyUI → 启动 → 探活 → 导出 object_info → 打 .stack_ready
# 用法: nohup bash build_and_start.sh > /data/build.log 2>&1 &
set -uo pipefail
TS() { date "+%H:%M:%S"; }
VENV=/root/comfyui-venv
COMFY=/root/ComfyUI

echo "[$(TS)] 开始安装 ComfyUI"
bash /root/install_comfyui.sh
if [ ! -f /data/.comfyui_installed ]; then
  echo "[$(TS)] !!! 安装未完成，停止"; exit 1
fi

echo "[$(TS)] 启动无头 ComfyUI"
pkill -f "ComfyUI/main.py" 2>/dev/null || true
sleep 2
cd "$COMFY"
nohup "$VENV/bin/python" main.py --listen 0.0.0.0 --port 8188 > /data/comfyui.log 2>&1 &
echo "[$(TS)] ComfyUI PID=$!"

for i in $(seq 1 90); do
  if curl -fsS -o /dev/null http://127.0.0.1:8188/system_stats 2>/dev/null; then
    echo "[$(TS)] ComfyUI 在线 ✅"
    curl -fsS http://127.0.0.1:8188/object_info > /data/object_info.json 2>/dev/null
    echo "[$(TS)] object_info 已导出 $(wc -c < /data/object_info.json) 字节"
    touch /data/.stack_ready
    echo "[$(TS)] STACK_READY"
    exit 0
  fi
  sleep 5
done
echo "[$(TS)] !!! ComfyUI 启动超时，看 /data/comfyui.log"; tail -30 /data/comfyui.log
exit 1
