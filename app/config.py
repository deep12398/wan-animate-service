"""集中配置，全部可用环境变量覆盖。"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WAN_", env_file=".env", extra="ignore")

    # --- ComfyUI 后端 ---
    comfy_base_url: str = "http://comfyui:8188"   # compose 网络里的服务名
    comfy_input_dir: str = "/data/comfyui-input"  # 与 ComfyUI 容器共享的输入目录
    comfy_output_dir: str = "/data/comfyui-output" # 与 ComfyUI 容器共享的输出目录

    # --- 本服务存储 ---
    data_dir: str = "/data/api"                   # 任务元数据 / 上传暂存
    workflow_path: str = "/app/workflow_api.json" # API 格式工作流模板

    # --- 任务/并发 ---
    # 单张 A10 只能串行跑推理，worker 固定 1；多卡时调大并配合多 ComfyUI 实例
    max_workers: int = 1
    job_timeout_s: int = 1200                      # 单任务硬超时（20 分钟）
    poll_interval_s: float = 2.0                   # 轮询 ComfyUI history 间隔

    # --- 默认生成参数（可被请求覆盖）---
    default_width: int = 480
    default_height: int = 832                       # 竖屏 480p，与文档实测一致
    default_frames: int = 77                        # ≈4.8s @16fps
    default_steps: int = 4                          # lightx2v 4 步加速(实测最快)
    default_seed: int = 42
    default_prompt: str = "a person dancing, soft 3D render style, high quality"

    # --- OpenTelemetry ---
    otel_service_name: str = "wan-animate-api"
    # 留空则只打到 stdout（console exporter），填了就走 OTLP/HTTP
    otel_exporter_otlp_endpoint: str = ""


settings = Settings()
