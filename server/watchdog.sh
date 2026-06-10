#!/usr/bin/env bash
# 自检看门狗：探活 + 自愈 + 周期性冒烟生成，全程记录健康日志。
# 由 cron 每 5 分钟跑一次。冒烟生成(真出片)默认每 6 小时一次(避免持续占 GPU)。
#
# 产出：
#   /data/health.log          追加式健康日志(人读)
#   /data/health_status.json  最新状态快照(机读/给网关探测)
#   /data/last_smoke.txt      上次冒烟成功的时间戳
set -uo pipefail

LOG=/data/health.log
STATUS=/data/health_status.json
SMOKE_STAMP=/data/last_smoke.txt
SMOKE_INTERVAL=${SMOKE_INTERVAL:-21600}   # 6 小时
COMFY=http://127.0.0.1:8188
API=http://127.0.0.1:8000
TS() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(TS)] $*" >> "$LOG"; }

overall=ok
declare -A st

# ---- 1. ComfyUI 探活(挂了自愈) ----
if curl -fsS -m 10 -o /dev/null "$COMFY/system_stats" 2>/dev/null; then
  st[comfyui]=up
else
  st[comfyui]=down; overall=degraded
  log "ComfyUI 无响应 → systemctl restart comfyui"
  systemctl restart comfyui
fi

# ---- 2. FastAPI 探活(挂了自愈) ----
if curl -fsS -m 10 -o /dev/null "$API/healthz" 2>/dev/null; then
  st[api]=up
else
  st[api]=down; overall=degraded
  log "FastAPI 无响应 → systemctl restart wan-api"
  systemctl restart wan-api
fi

# ---- 3. GPU 可用性 ----
if nvidia-smi >/dev/null 2>&1; then
  st[gpu]=up
  gpu_mem=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
else
  st[gpu]=down; overall=critical; gpu_mem=0
  log "nvidia-smi 失败 — GPU 驱动异常!"
fi

# ---- 4. 磁盘水位 ----
disk_pct=$(df / | awk 'NR==2{print $5}' | tr -d '%')
if [ "${disk_pct:-0}" -gt 90 ]; then
  st[disk]=full; overall=degraded
  log "磁盘 ${disk_pct}% — 接近满,清理 /data/comfyui-output 旧文件"
  # 清理 7 天前的产物，避免撑爆
  find /data/comfyui-output -type f -mtime +7 -delete 2>/dev/null
else
  st[disk]=ok
fi

# ---- 5. 周期性冒烟生成(真出片，验证整条推理链) ----
smoke=skipped
now=$(date +%s)
last=$(cat "$SMOKE_STAMP" 2>/dev/null || echo 0)
# ComfyUI 队列繁忙(有真实任务在跑)时跳过冒烟，不抢单卡 GPU
qlen=$(curl -fsS -m 8 "$COMFY/queue" 2>/dev/null | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d.get('queue_running',[]))+len(d.get('queue_pending',[])))" 2>/dev/null || echo 1)
if [ "${st[comfyui]}" = "up" ] && [ "${st[api]}" = "up" ] && [ "${qlen:-1}" = "0" ] && [ $((now - last)) -ge "$SMOKE_INTERVAL" ]; then
  log "冒烟生成开始(默认素材)..."
  resp=$(curl -fsS -m 30 -X POST "$API/api/v1/jobs" \
    -F "image=@/data/comfyui-input/default_image.jpg" \
    -F "video=@/data/comfyui-input/default_video.mp4" 2>/dev/null)
  jid=$(echo "$resp" | python3 -c "import json,sys;print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)
  if [ -n "$jid" ]; then
    # 轮询最多 20 分钟
    for i in $(seq 1 120); do
      s=$(curl -fsS -m 10 "$API/api/v1/jobs/$jid" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
      [ "$s" = "done" ] && { smoke=pass; echo "$now" > "$SMOKE_STAMP"; log "冒烟生成成功 job=$jid"; break; }
      [ "$s" = "error" ] && { smoke=fail; overall=degraded; log "冒烟生成失败 job=$jid"; break; }
      sleep 10
    done
    [ "$smoke" = "skipped" ] && { smoke=timeout; overall=degraded; log "冒烟生成超时 job=$jid"; }
  else
    smoke=submit_fail; overall=degraded; log "冒烟生成提交失败: $resp"
  fi
fi

# ---- 写状态快照 ----
cat > "$STATUS" <<EOF
{
  "ts": "$(TS)",
  "overall": "$overall",
  "comfyui": "${st[comfyui]}",
  "api": "${st[api]}",
  "gpu": "${st[gpu]}",
  "gpu_mem_mb": ${gpu_mem:-0},
  "disk_pct": ${disk_pct:-0},
  "smoke": "$smoke"
}
EOF

# 只在异常或冒烟跑过时写一行汇总日志，避免日志爆炸
if [ "$overall" != "ok" ] || [ "$smoke" != "skipped" ]; then
  log "汇总 overall=$overall comfyui=${st[comfyui]} api=${st[api]} gpu=${st[gpu]} disk=${disk_pct}% smoke=$smoke"
fi
