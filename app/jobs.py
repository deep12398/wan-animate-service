"""任务存储 + 单 worker 异步队列。

为什么是单 worker：单张 A10 一次只能跑一个推理。真正的高并发由团队 Golang 网关
在前面排队/分发，本服务只需保证「收得下、串行跑、状态可查」，不丢请求即可。
多卡扩容时，把 max_workers 调大并对接多个 ComfyUI 实例即可。

任务元数据落盘到 data_dir/jobs/<id>.json，进程重启后可恢复查询（运行中的任务会标记为 interrupted）。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum

from opentelemetry import trace

from .comfy_client import ComfyClient, ComfyError
from .config import settings
from .workflow import GenParams, WorkflowTemplate

tracer = trace.get_tracer(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass
class Job:
    id: str
    status: JobStatus
    params: dict
    created_at: float
    updated_at: float
    prompt_id: str | None = None
    result_filename: str | None = None   # 对外可下载的文件名
    result_path: str | None = None       # 产物实际磁盘绝对路径
    error: str | None = None
    timings: dict = field(default_factory=dict)

    def touch(self, status: JobStatus | None = None):
        if status:
            self.status = status
        self.updated_at = time.time()


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._comfy = ComfyClient()
        self._workflow = WorkflowTemplate()
        self._jobs_dir = os.path.join(settings.data_dir, "jobs")
        os.makedirs(self._jobs_dir, exist_ok=True)
        os.makedirs(settings.comfy_input_dir, exist_ok=True)

    # ---------- 生命周期 ----------
    async def start(self):
        for _ in range(settings.max_workers):
            self._workers.append(asyncio.create_task(self._worker_loop()))

    async def stop(self):
        for w in self._workers:
            w.cancel()
        await self._comfy.aclose()

    # ---------- 对外 API ----------
    def create(self, params: GenParams) -> Job:
        job = Job(
            id=uuid.uuid4().hex,
            status=JobStatus.QUEUED,
            params=asdict(params),
            created_at=time.time(),
            updated_at=time.time(),
        )
        self._jobs[job.id] = job
        self._persist(job)
        self._queue.put_nowait(job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id) or self._load(job_id)

    async def comfy_healthy(self) -> bool:
        return await self._comfy.health()

    # ---------- worker ----------
    async def _worker_loop(self):
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if not job:
                continue
            await self._run_job(job)
            self._queue.task_done()

    async def _run_job(self, job: Job):
        with tracer.start_as_current_span("job.run") as span:
            span.set_attribute("job.id", job.id)
            t0 = time.time()
            try:
                job.touch(JobStatus.RUNNING)
                self._persist(job)

                params = GenParams(**job.params)
                workflow = self._workflow.build(params)
                primary_node = self._workflow.primary_output_node_id(workflow)

                t_submit = time.time()
                job.prompt_id = await self._comfy.submit(workflow)
                self._persist(job)

                result = await self._comfy.wait(job.prompt_id, primary_node_id=primary_node)
                # 取第一个产物：_collect_outputs 已把主输出(带 audio 的 VideoCombine)排在最前，
                # 不再是先完成的方形人脸裁剪预览。
                src = result.output_files[0]
                job.result_path = src
                job.result_filename = os.path.basename(src)
                job.timings = {
                    "queue_to_submit_s": round(t_submit - t0, 2),
                    "inference_s": round(time.time() - t_submit, 2),
                    "total_s": round(time.time() - t0, 2),
                }
                job.touch(JobStatus.DONE)
                span.set_attribute("job.inference_s", job.timings["inference_s"])
            except ComfyError as e:
                job.error = str(e)
                job.touch(JobStatus.ERROR)
                span.record_exception(e)
            except Exception as e:  # noqa: BLE001 兜底，保证 worker 不死
                job.error = f"{type(e).__name__}: {e}"
                job.touch(JobStatus.ERROR)
                span.record_exception(e)
            finally:
                self._persist(job)

    # ---------- 持久化 ----------
    def _persist(self, job: Job):
        d = asdict(job)
        d["status"] = job.status.value
        with open(os.path.join(self._jobs_dir, f"{job.id}.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

    def _load(self, job_id: str) -> Job | None:
        path = os.path.join(self._jobs_dir, f"{job_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        d["status"] = JobStatus(d["status"])
        return Job(**d)
