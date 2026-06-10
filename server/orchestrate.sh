#!/usr/bin/env bash
# 无人值守编排：等模型下载完 → 装 ComfyUI → 启动并探活 → 打 ready 标记
# 用法: nohup bash orchestrate.sh > /data/orchestrate.log 2>&1 &
set -uo pipefail
TS() { date "+%H:%M:%S"; }
VENV=/root/comfyui-venv
COMFY=/root/ComfyUI

echo "[$(TS)] 编排启动，等待模型下载完成..."
# 1. 等下载完（download_models.sh 完成会建 .download_complete）
for i in $(seq 1 240); do          # 最多等 2 小时
  [ -f /data/comfyui-models/.download_complete ] && break
  sleep 30
done
if [ ! -f /data/comfyui-models/.download_complete ]; then
  echo "[$(TS)] !!! 下载未完成，放弃"; exit 1
fi
echo "[$(TS)] 模型下载完成，开始装 ComfyUI（独占带宽）"

# 2. 装 ComfyUI（带宽已空闲，pip 不会再被抢）
bash /root/install_comfyui.sh
if [ ! -f /data/.comfyui_installed ]; then
  echo "[$(TS)] !!! ComfyUI 安装未完成"; exit 1
fi

# 3. 启动无头 ComfyUI
echo "[$(TS)] 启动 ComfyUI ..."
pkill -f "ComfyUI/main.py" 2>/dev/null || true
sleep 2
cd "$COMFY"
nohup "$VENV/bin/python" main.py --listen 0.0.0.0 --port 8188 > /data/comfyui.log 2>&1 &
echo "[$(TS)] ComfyUI PID=$!"

# 4. 探活（最多等 5 分钟让它加载节点）
for i in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8188/system_stats 2>/dev/null; then
    echo "[$(TS)] ComfyUI 在线 ✅"
    touch /data/.stack_ready
    # 顺带导出已注册的节点清单，便于校验 Animate 节点都在
    curl -fsS http://127.0.0.1:8188/object_info > /data/object_info.json 2>/dev/null
    echo "[$(TS)] STACK_READY"
    exit 0
  fi
  sleep 5
done
echo "[$(TS)] !!! ComfyUI 启动超时，看 /data/comfyui.log"
exit 1
