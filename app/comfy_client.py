"""ComfyUI HTTP API 客户端。

无头 ComfyUI 通过 /prompt 接口接收 "API 格式" 工作流（一个 node_id -> {class_type, inputs} 的扁平字典），
排队执行后把结果写进 output 目录。本客户端负责：提交、轮询、定位产物。

输入/输出文件走共享卷直接落盘（comfy_input_dir / comfy_output_dir），
不用 ComfyUI 的 /upload 接口，省一次网络拷贝也避开 VHS 上传的格式坑。
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

import httpx
from opentelemetry import trace

from .config import settings

tracer = trace.get_tracer(__name__)


class ComfyError(RuntimeError):
    pass


@dataclass
class ComfyResult:
    prompt_id: str
    output_files: list[str]   # output 目录下的绝对路径


class ComfyClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.comfy_base_url).rstrip("/")
        self.client_id = uuid.uuid4().hex
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def aclose(self):
        await self._http.aclose()

    async def health(self) -> bool:
        """ComfyUI 是否在线（/system_stats 是个轻量探针）。"""
        try:
            r = await self._http.get("/system_stats")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def submit(self, workflow_api: dict) -> str:
        """提交工作流，返回 prompt_id。"""
        with tracer.start_as_current_span("comfy.submit"):
            payload = {"prompt": workflow_api, "client_id": self.client_id}
            r = await self._http.post("/prompt", json=payload)
            if r.status_code != 200:
                # ComfyUI 校验失败会在这里返回 400 + 详细 node_errors
                raise ComfyError(f"提交失败 {r.status_code}: {r.text[:1000]}")
            data = r.json()
            pid = data.get("prompt_id")
            if not pid:
                raise ComfyError(f"未拿到 prompt_id: {data}")
            return pid

    async def wait(self, prompt_id: str, timeout_s: int | None = None) -> ComfyResult:
        """轮询 /history 直到该 prompt 完成，返回产物路径。

        踩坑防御（对应部署文档 8.3）：ComfyUI 可能 status=success 但其实静默跳过了节点、
        没产出视频。所以这里不仅看 history 出现，还要校验 outputs 里确实有视频文件。
        """
        timeout_s = timeout_s or settings.job_timeout_s
        deadline = asyncio.get_event_loop().time() + timeout_s
        with tracer.start_as_current_span("comfy.wait") as span:
            span.set_attribute("comfy.prompt_id", prompt_id)
            while True:
                r = await self._http.get(f"/history/{prompt_id}")
                if r.status_code == 200:
                    hist = r.json().get(prompt_id)
                    if hist:
                        status = hist.get("status", {})
                        if status.get("status_str") == "error":
                            raise ComfyError(f"ComfyUI 执行报错: {status}")
                        if status.get("completed", False) or hist.get("outputs"):
                            files = self._collect_outputs(hist.get("outputs", {}))
                            if files:
                                span.set_attribute("comfy.output_count", len(files))
                                return ComfyResult(prompt_id, files)
                            # 完成但没视频 = 静默跳过坑
                            raise ComfyError(
                                "ComfyUI 报告完成但没有产出视频——大概率某节点缺模型被静默跳过，"
                                "检查 ComfyUI 日志 'Value not in list'。"
                            )
                if asyncio.get_event_loop().time() > deadline:
                    raise ComfyError(f"等待超时 {timeout_s}s (prompt_id={prompt_id})")
                await asyncio.sleep(settings.poll_interval_s)

    def _collect_outputs(self, outputs: dict) -> list[str]:
        """从 history.outputs 里挑出视频产物的实际磁盘路径。

        优先用 ComfyUI 给的 'fullpath'(最可靠，自动兼容 output/temp 目录)；
        没有时按 type 回退到对应目录拼路径。
        """
        files: list[str] = []
        for node_out in outputs.values():
            # VHS_VideoCombine 产物在 'gifs'，SaveVideo 在 'videos'/'images'
            for key in ("gifs", "videos", "images"):
                for item in node_out.get(key, []) or []:
                    fn = item.get("filename")
                    if not fn or not fn.lower().endswith((".mp4", ".webm", ".gif")):
                        continue
                    path = item.get("fullpath")
                    if not path:
                        base = (
                            settings.comfy_output_dir
                            if item.get("type") != "temp"
                            else os.path.join(os.path.dirname(settings.comfy_output_dir), "comfyui-temp")
                        )
                        path = os.path.join(base, item.get("subfolder", ""), fn)
                    if os.path.exists(path):
                        files.append(path)
        return files

    async def interrupt(self):
        """中断当前执行（用于取消任务）。"""
        try:
            await self._http.post("/interrupt")
        except httpx.HTTPError:
            pass
