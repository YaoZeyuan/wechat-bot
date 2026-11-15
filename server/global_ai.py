"""Default model configuration for plugins without a dedicated model route."""

import json
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, SecretStr

from .plugin.ai_chat import ChatError, Model, Provider, complete

PATH = Path(__file__).resolve().parents[1] / "local-data" / "global-ai.json"
TRIAL_PATH = PATH.with_name('trial-ai.json')
LOCK = Lock()
DEFAULT_PROMPT = (Path(__file__).parent / "plugin" / "chat_summary" / "default_prompt.md").read_text(encoding="utf-8")


class Settings(BaseModel):
    provider: Literal["deepseek", "doubao", "compatible"] = "deepseek"
    base_url: HttpUrl = HttpUrl("https://api.deepseek.com")
    api_key: SecretStr = SecretStr("")
    model: str = Field(default="", max_length=256)
    thinking: Literal["disabled", "enabled"] | None = "disabled"
    timeout_seconds: float = Field(default=600, gt=0, le=600)
    summary_prompt: str = Field(default=DEFAULT_PROMPT, min_length=1, max_length=16000)


def load(path=PATH) -> Settings:
    if not path.exists():
        if path == TRIAL_PATH:
            return Settings(provider="compatible", model="qwen3.7-flash", thinking=None, timeout_seconds=600)
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if path == TRIAL_PATH and isinstance(data, dict):
            data = {"model": "qwen3.7-flash", "timeout_seconds": 600, **data, "thinking": None}
        return Settings.model_validate(data)
    except (OSError, ValueError) as exc:
        raise ChatError("无法读取全局 AI 配置") from exc


def save(settings: Settings, path=PATH):
    with LOCK:
        data = settings.model_dump(mode="json", exclude={"api_key"})
        data["api_key"] = settings.api_key.get_secret_value() or load(path).api_key.get_secret_value()
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pending.replace(path)


def public_settings(path=PATH, *, include_api_key=False) -> dict:
    settings = load(path)
    return {**settings.model_dump(mode="json", exclude={"api_key"}),
            "has_api_key": bool(settings.api_key.get_secret_value()),
            **({"api_key": settings.api_key.get_secret_value()} if include_api_key else {})}


def summarize(transcript: str, request_id: str) -> str:
    return generate(transcript, request_id, summary=True)[0]


def generate(prompt: str, request_id: str, *, summary=False, model_id=None, trial=False, on_request=None):
    settings = load(TRIAL_PATH if trial else PATH)
    if trial and not settings.api_key.get_secret_value().strip():
        raise ChatError("请在插件管理 → 审判中配置聚合 API 的密钥")
    model = Model(id=(model_id or settings.model).strip(), thinking=settings.thinking)
    provider = Provider(base_url=settings.base_url, api_key=settings.api_key, models={},
                        timeout_seconds=settings.timeout_seconds)
    answer, elapsed = complete(provider, model, settings.provider, prompt, settings.summary_prompt if summary else "",
                           request_id=request_id, on_request=on_request)
    return answer, model.id, elapsed
