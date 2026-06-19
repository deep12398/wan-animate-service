"""Wan2.2-Animate 换脸跳舞 — REST API 服务。

对外契约（供团队 Golang 网关异步调用）：
  POST /api/v1/jobs            提交任务（头像图 + 驱动视频）→ {job_id, status}
  GET  /api/v1/jobs/{id}       查询状态/耗时/结果文件名
  GET  /api/v1/jobs/{id}/result  下载生成的 MP4（done 后可用）
  GET  /healthz /readyz        存活/就绪探针
"""
from __future__ import annotations

import os
import shutil
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .config import settings
from .jobs import JobStore
from .telemetry import init_telemetry, instrument_app
from .workflow import ASPECT_RATIOS, GenParams

# OTel 必须在建 app 前初始化
init_telemetry()

store: JobStore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    store = JobStore()
    await store.start()
    yield
    await store.stop()


app = FastAPI(title="Wan2.2-Animate API", version="1.0.0", lifespan=lifespan)
instrument_app(app)


def _save_upload(upload: UploadFile, kind: str) -> str:
    """把上传文件落到 ComfyUI 共享输入目录，返回文件名。"""
    ext = os.path.splitext(upload.filename or "")[1] or (".jpg" if kind == "image" else ".mp4")
    fname = f"{kind}_{uuid.uuid4().hex}{ext}"
    dest = os.path.join(settings.comfy_input_dir, fname)
    with open(dest, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    return fname


@app.post("/api/v1/jobs")
async def create_job(
    image: UploadFile = File(..., description="人物头像图 JPG/PNG"),
    video: UploadFile = File(..., description="驱动舞蹈视频 MP4"),
    prompt: str = Form(settings.default_prompt),
    aspect_ratio: str = Form(settings.default_aspect_ratio, description="输出比例: 9:16 / 3:4 / 1:1"),
    width: int = Form(0, description="高级覆盖: 与 height 同时 >0 才生效，压过 aspect_ratio"),
    height: int = Form(0, description="高级覆盖: 与 width 同时 >0 才生效，压过 aspect_ratio"),
    frames: int = Form(settings.default_frames),
    seed: int = Form(settings.default_seed),
    steps: int = Form(settings.default_steps),
):
    assert store is not None
    # 校验比例：仅当未走显式 width/height 覆盖时，aspect_ratio 必须合法
    if not (width > 0 and height > 0) and aspect_ratio not in ASPECT_RATIOS:
        raise HTTPException(
            422,
            f"不支持的 aspect_ratio={aspect_ratio!r}，允许值: {', '.join(ASPECT_RATIOS)}",
        )
    image_fn = _save_upload(image, "image")
    video_fn = _save_upload(video, "video")
    params = GenParams(
        image_filename=image_fn,
        video_filename=video_fn,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        width=width,
        height=height,
        frames=frames,
        seed=seed,
        steps=steps,
    )
    job = store.create(params)
    return JSONResponse(
        status_code=202,
        content={"job_id": job.id, "status": job.status.value},
    )


@app.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: str):
    assert store is not None
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {
        "job_id": job.id,
        "status": job.status.value,
        "prompt_id": job.prompt_id,
        "result_filename": job.result_filename,
        "error": job.error,
        "timings": job.timings,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


@app.get("/api/v1/jobs/{job_id}/result")
async def get_result(job_id: str):
    assert store is not None
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status.value != "done" or not job.result_path:
        raise HTTPException(409, f"job not finished (status={job.status.value})")
    if not os.path.exists(job.result_path):
        raise HTTPException(410, "result file missing on disk")
    return FileResponse(job.result_path, media_type="video/mp4", filename=job.result_filename)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    assert store is not None
    ok = await store.comfy_healthy()
    if not ok:
        raise HTTPException(503, "comfyui not reachable")
    return {"status": "ready"}
