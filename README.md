# Wan2.2-Animate 换脸跳舞 — 商用推理服务

一张人物头像 + 一段驱动舞蹈视频 → 该人物按视频动作跳舞的新视频。
本服务把**无头 ComfyUI 当推理引擎**，外面包一层 **FastAPI**（REST API + OpenTelemetry 埋点），
供团队 **Golang 高并发网关**异步调用。

---

## 为什么是「无头 ComfyUI + FastAPI」而不是纯 diffusers

| 关键事实 | 结论 |
|---------|------|
| A10 是 Ampere(CC 8.6)，diffusers 的 FP8 量化要 CC≥8.9(Ada/Hopper) | **A10 上 diffusers 拿不到 FP8 加速**，只能 bf16+offload，单条好几分钟 |
| diffusers 不含 DWPose/人脸预处理(官方说"未来才集成") | 纯 diffusers 要自己重写预处理，周期数周 |
| ComfyUI 工作流已含完整预处理 + 6 步 lightx2v 加速 | 同卡实测 ~144s/条，开箱即用 |
| "拒绝 ComfyUI" 实为拒绝其浏览器界面 | ComfyUI 可 `--listen` 无头运行，纯当推理后端，用户永远看不到它 |
| 高并发由队列+多 worker 解决，与推理框架无关 | Golang 网关排队 + 单卡串行，框架不影响并发能力 |

> 结论：A10 上 ComfyUI 后端是**低风险、零质量损失、几天上线**的方案；
> 纯 diffusers 的优势(代码可控/易扩多卡)只有换到 Ada/Hopper 卡(L40S/L4)才真正兑现。

---

## 架构

```
团队 Golang 高并发网关
      │  HTTP 异步调用
      ▼
FastAPI (本服务, 端口 8000)  ── OpenTelemetry Trace ──▶ Collector
      │  内部 asyncio 队列(单 worker, 单卡串行)
      │  /prompt 提交 + /history 轮询
      ▼
无头 ComfyUI (127.0.0.1:8188, 不暴露公网)
      │  DWPose 预处理 → Wan2.2-Animate-14B fp8 → 6步 lightx2v → VAE 解码
      ▼
/data/comfyui-output/*.mp4  ──FastAPI 回传──▶ 网关
```

## 目录结构

```
wan-animate-service/
├── app/                    # FastAPI 服务
│   ├── main.py             #   路由: 提交/查询/下载/健康检查
│   ├── jobs.py             #   任务存储 + 单 worker 异步队列
│   ├── comfy_client.py     #   ComfyUI HTTP 客户端(提交/轮询/取产物)
│   ├── workflow.py         #   工作流参数注入(按 class_type 定位, 不重写 JSON)
│   ├── telemetry.py        #   OpenTelemetry 初始化
│   ├── config.py           #   配置(全部可 WAN_* 环境变量覆盖)
│   └── requirements.txt
├── docker/
│   ├── Dockerfile.comfyui  # GPU 推理引擎镜像
│   └── Dockerfile.api      # FastAPI 网关镜像(CPU)
├── docker-compose.yml      # 两服务编排 + 卷挂载
├── server/
│   ├── download_models.sh  # 模型下载(URL 已实测验证)
│   └── install_comfyui.sh  # 宿主机直装 ComfyUI(venv)
├── workflows/              # ComfyUI 官方 Animate 示例工作流
└── scripts/test_api.sh     # 端到端测试
```

## API 契约

### 提交任务
```
POST /api/v1/jobs   (multipart/form-data)
  image         头像图 (JPG/PNG)         必填
  video         驱动视频 (MP4)           必填
  prompt        文本提示                 默认: a person dancing...
  aspect_ratio  输出比例                 默认 9:16 (可选 3:4 / 1:1)
  frames        帧数 默认 77 (≈4.8s@16fps)
  seed          随机种子 默认 42
  steps         采样步数 默认 6 (lightx2v加速)
  width/height  高级覆盖(可选)：两者都 >0 才生效，压过 aspect_ratio；须能被 16 整除
→ 202 {"job_id": "...", "status": "queued"}
```

**比例 → 分辨率**（480 宽档）：`9:16`→480×848，`3:4`→480×640，`1:1`→640×640。
不传 `aspect_ratio` 时默认 `9:16`；传非法值返回 `422` 并列出允许值。

示例（同事直接对接）：
```bash
curl -X POST http://<host>:8000/api/v1/jobs \
  -F image=@头像.jpg -F video=@舞蹈.mp4 \
  -F aspect_ratio=9:16
# 3:4 改成 -F aspect_ratio=3:4 即可
```

### 查询状态
```
GET /api/v1/jobs/{job_id}
→ {"status": "queued|running|done|error", "timings": {...}, "result_filename": "...", "error": null}
```

### 下载结果
```
GET /api/v1/jobs/{job_id}/result   → video/mp4
```

### 探针
```
GET /healthz   存活
GET /readyz    就绪(检查 ComfyUI 可达)
```

## 部署

### 方式一：Docker Compose（生产推荐）
```bash
# 模型已在 /data/comfyui-models/（download_models.sh 下好）
cd wan-animate-service
docker compose up -d --build
# 验证
curl localhost:8000/readyz
bash scripts/test_api.sh 头像.jpg 舞蹈.mp4
```

### 方式二：宿主机 venv（调试用）
```bash
bash server/install_comfyui.sh                 # 装 ComfyUI
/root/comfyui-venv/bin/python /root/ComfyUI/main.py --listen 127.0.0.1 --port 8188 &
cd wan-animate-service
pip install -r app/requirements.txt
WAN_COMFY_BASE_URL=http://127.0.0.1:8188 \
WAN_COMFY_INPUT_DIR=/data/comfyui-input \
WAN_COMFY_OUTPUT_DIR=/data/comfyui-output \
  uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 关键工程决策

- **attention=sdpa**：A10(sm_86) 上 sageattn 会 `no kernel image` 崩溃，强制用 PyTorch 原生 sdpa。
- **fp8 文本编码器 + blockswap**：14B 模型在 24G 卡内靠 fp8 + block swap(25 块) 容纳，峰值显存 ~15G。
- **单 worker 串行**：单 A10 一次只跑一个推理，并发由 Golang 网关排队；多卡再调 `WAN_MAX_WORKERS`。
- **静默跳过防御**：ComfyUI 可能 status=success 却没产视频(缺模型)，客户端校验产物存在才算成功。

## 扩容路线

| 阶段 | 动作 |
|------|------|
| MVP | 当前 A10 单卡，~144s/条 |
| 提速 | 换 L40S(Ada 48G)：原生 FP8 + 无需 offload，~60-90s/条，吞吐翻倍 |
| 多卡 | N 张卡各跑一个 ComfyUI 实例，FastAPI 前置负载均衡 |
| 存储 | 产物上传阿里云 OSS，返回 URL 而非文件流 |
