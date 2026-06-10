# 对接文档

> 一张人物头像 + 一段舞蹈视频 → 该人物按视频动作跳舞的新视频。
> 本文命令均已实测跑通。

## 一、服务地址

```
http://<服务器IP>:8000
```
> 线上测试地址 + IP 白名单请向项目负责人索取。自部署见本仓库 [README](../README.md)。

## 二、核心概念：这是「异步任务」接口

生成一条视频要 **5-6 分钟**，远超 HTTP 超时。所以**不是请求即响应，而是三步**：

```
① 提交 → 拿 job_id    ② 轮询状态直到 done    ③ 下载成片
```
单卡串行：一次只跑一个任务，并发请求会在服务内部排队（真正的并发调度建议在网关层做）。

## 三、接口契约

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/v1/jobs` | POST (multipart) | 提交任务，返回 job_id |
| `/api/v1/jobs/{job_id}` | GET | 查状态 |
| `/api/v1/jobs/{job_id}/result` | GET | 下载 mp4（done 后） |
| `/healthz` `/readyz` | GET | 存活/就绪探针 |

**提交参数**（multipart/form-data）：

| 字段 | 必填 | 默认 | 说明 |
|------|:--:|------|------|
| `image` | ✓ | - | 人物头像 JPG/PNG |
| `video` | ✓ | - | 驱动舞蹈视频 MP4（5-10秒，单人入镜） |
| `prompt` | | a person dancing... | 文本提示 |
| `seed` | | 42 | 随机种子 |
| `steps` | | 4 | 采样步数 |

**状态机**：`queued`（排队）→ `running`（生成中）→ `done`（完成）/ `error`（失败）

## 四、实测可跑的命令

```bash
API=http://<服务器IP>:8000

# ① 提交
RESP=$(curl -fsS -X POST $API/api/v1/jobs \
  -F image=@头像.jpg \
  -F video=@舞蹈.mp4 \
  -F "prompt=a person dancing, soft 3D render style")
echo $RESP
# → {"job_id":"a1909fc4...","status":"queued"}

# ② 轮询（JOB 换成上面的 job_id）
JOB=<job_id>
curl -s $API/api/v1/jobs/$JOB
# → {"status":"running",...}  每 8-10 秒查一次，等到 "done"

# ③ 下载
curl -fsS $API/api/v1/jobs/$JOB/result -o out.mp4
```

成片规格：832×480 / 77帧 / ~4.8秒 / h264 mp4。

## 五、Go 网关对接骨架

```go
const api = "http://<服务器IP>:8000"

// ① 提交
body := &bytes.Buffer{}
w := multipart.NewWriter(body)
fw, _ := w.CreateFormFile("image", "face.jpg"); io.Copy(fw, imgReader)
fw, _ = w.CreateFormFile("video", "dance.mp4"); io.Copy(fw, vidReader)
w.WriteField("prompt", "a person dancing")
w.Close()
resp, _ := http.Post(api+"/api/v1/jobs", w.FormDataContentType(), body)
// 解析 job_id

// ② 轮询（网关侧也应把任务入自己的队列，别让前端 HTTP 连接挂 5 分钟）
for {
    r, _ := http.Get(api + "/api/v1/jobs/" + jobID)
    // status: queued/running/done/error
    if status == "done" { break }
    if status == "error" { /* 取 error 字段报错 */ }
    time.Sleep(8 * time.Second)
}

// ③ 下载
r, _ := http.Get(api + "/api/v1/jobs/" + jobID + "/result")
io.Copy(outFile, r.Body)
```

## 六、注意事项

- **目前无鉴权**，靠安全组 IP 白名单保护；正式对接前会加 API Key。
- **单条 5-6 分钟**，失败时 `status=error` 且 `error` 字段有原因。
- 驱动视频建议 **单人、正面、5-10 秒**；头像建议清晰正脸。
- 想要更快（2-3 分钟）需把服务器从 A10 升级到 L40S（详见 README 的"扩容路线"）。
