#!/usr/bin/env bash
# 端到端测试：提交一个换脸跳舞任务，轮询直到完成，拉回视频
# 用法: bash test_api.sh <头像图> <驱动视频> [API地址]
set -euo pipefail

IMG="${1:?用法: test_api.sh <头像图.jpg> <驱动视频.mp4> [http://localhost:8000]}"
VID="${2:?缺少驱动视频}"
API="${3:-http://localhost:8000}"

echo ">>> 1. 健康检查"
curl -fsS "$API/readyz" && echo

echo ">>> 2. 提交任务"
RESP=$(curl -fsS -X POST "$API/api/v1/jobs" \
  -F "image=@$IMG" \
  -F "video=@$VID" \
  -F "prompt=a person dancing, soft 3D render style, high quality" \
  -F "width=480" -F "height=832" -F "frames=77" -F "steps=6")
echo "$RESP"
JOB=$(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['job_id'])")
echo "job_id=$JOB"

echo ">>> 3. 轮询状态"
while true; do
  S=$(curl -fsS "$API/api/v1/jobs/$JOB")
  ST=$(echo "$S" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
  echo "  status=$ST  $(echo "$S" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('timings') or '')")"
  [ "$ST" = "done" ] && break
  [ "$ST" = "error" ] && { echo "失败: $S"; exit 1; }
  sleep 5
done

echo ">>> 4. 下载结果"
OUT="result_${JOB}.mp4"
curl -fsS "$API/api/v1/jobs/$JOB/result" -o "$OUT"
echo "已保存: $OUT ($(du -h "$OUT" | cut -f1))"
