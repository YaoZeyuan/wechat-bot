"""Default model configuration for plugins without a dedicated model route."""

import json
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, SecretStr

from .plugin.ai_chat import ChatError, Model, Provider, complete

PATH = Path(__file__).resolve().parents[1] / "local-data" / "global-ai.json"
LOCK = Lock()
DEFAULT_PROMPT = (Path(__file__).parent / "plugin" / "chat_summary" / "default_prompt.md").read_text(encoding="utf-8")


class Settings(BaseModel):
    provider: Literal["deepseek", "doubao", "compatible"] = "deepseek"
    base_url: HttpUrl = HttpUrl("https://api.deepseek.com")
    api_key: SecretStr = SecretStr("")
    model: str = Field(default="", max_length=256)
    thinking: Literal["disabled", "enabled"] | None = "disabled"
    timeout_seconds: float = Field(default=120, gt=0, le=600)
    summary_prompt: str = Field(default=DEFAULT_PROMPT, min_length=1, max_length=16000)


def load() -> Settings:
    if not PATH.exists():
        return Settings()
    try:
        return Settings.model_validate_json(PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ChatError("无法读取全局 AI 配置") from exc


def save(settings: Settings):
    with LOCK:
        data = settings.model_dump(mode="json", exclude={"api_key"})
        data["api_key"] = settings.api_key.get_secret_value() or load().api_key.get_secret_value()
        PATH.parent.mkdir(parents=True, exist_ok=True)
        pending = PATH.with_suffix(".tmp")
        pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pending.replace(PATH)


def public_settings() -> dict:
    settings = load()
    return {**settings.model_dump(mode="json", exclude={"api_key"}),
            "has_api_key": bool(settings.api_key.get_secret_value())}


def summarize(transcript: str, request_id: str) -> str:
    return generate(transcript, request_id, summary=True)[0]


def generate(prompt: str, request_id: str, *, summary=False):
    settings = load()
    model = Model(id=settings.model.strip(), thinking=settings.thinking)
    provider = Provider(base_url=settings.base_url, api_key=settings.api_key, models={},
                        timeout_seconds=settings.timeout_seconds)
    answer, elapsed = complete(provider, model, settings.provider, prompt, settings.summary_prompt if summary else "",
                           request_id=request_id)
    return answer, model.id, elapsed
