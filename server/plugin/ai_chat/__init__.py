"""Single-turn chat with an OpenAI-compatible model provider."""
import json
import re
from pathlib import Path
from time import perf_counter
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr
import structlog

from ...ciphertalk import NoRedirect

PLUGIN_ID = "ai_chat"
METADATA = {
    "name": "AI 问答", "description": "按配置的触发前缀调用模型（不区分大小写）",
    "default_enabled": False, "version": "1.0.0", "capability": "模型 API、手机发送",
}
CONFIG = Path(__file__).resolve().parents[3] / "plugins" / "ai_chat" / "config.json"
log = structlog.get_logger("wechat_bot.ai_chat")
PREFIX_SEPARATORS = re.compile(r"^[\s,，、.。｡!！?？:：;；…‥—–－-]+")


class ChatError(RuntimeError):
    pass


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    thinking: Literal["disabled", "enabled"] | None = "disabled"
    web_search: bool = False


class Provider(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: HttpUrl
    api_key: SecretStr
    models: dict[str, str | Model]
    timeout_seconds: float = Field(default=20, gt=0, le=600)


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str
    prefixes: list[str] | None = None
    strip_prefixes: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, le=1)
    reply_prefix: str = Field(default="", max_length=200)
    providers: dict[str, Provider]
    routes: dict[str, ModelRoute]


def trigger(text: str) -> tuple[str, str] | None:
    try:
        settings = Settings.model_validate_json(CONFIG.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    candidates = []
    for route_id, route in settings.routes.items():
        strip_prefixes = {alias.strip().casefold() for alias in route.strip_prefixes}
        prefixes = [route_id] if route.prefixes is None else route.prefixes
        for alias in [*prefixes, *route.strip_prefixes]:
            alias = alias.strip()
            if not alias:
                continue
            match = re.match(re.escape(alias) + r"\s*", text, re.IGNORECASE)
            if match:
                strip_alias = alias.casefold() in strip_prefixes
                prompt = text[match.end():]
                if strip_alias:
                    prompt = PREFIX_SEPARATORS.sub("", prompt)
                if not prompt.strip():
                    continue
                content = prompt if strip_alias else text
                candidates.append((len(alias), route_id, content))
    if candidates:
        longest = max(length for length, _, _ in candidates)
        matches = {(route_id, prompt) for length, route_id, prompt in candidates if length == longest}
        if len(matches) == 1:
            return matches.pop()
    return None


def format_question(text: str, display_name: str, quotes: list[dict[str, str]]) -> str:
    sender = display_name or "提问者"
    return with_quotes(f"{sender}: {text}", quotes)


def with_quotes(prompt: str, quotes: list[dict[str, str]]) -> str:
    if not quotes:
        return prompt
    lines = [prompt, ""]
    for index, quote in enumerate(quotes[:3], 1):
        sender = f'{quote["sender"]}：' if quote.get("sender") else ""
        marker = ">" * index
        lines.extend(f"{marker} {line}" for line in (sender + quote["text"]).split("\n"))
    return "\n".join(lines)


def answer(prefix: str, prompt: str, *, request_id: str | None = None) -> str:
    try:
        settings = Settings.model_validate_json(CONFIG.read_text(encoding="utf-8-sig"))
        route = settings.routes[prefix]
        provider = settings.providers[route.provider]
        model = provider.models[route.model]
        if isinstance(model, str):
            model = Model(id=model)
    except (OSError, ValueError, KeyError):
        raise ChatError("请检查本地 plugins/ai_chat/config.json 中的服务商、触发词和模型配置") from None
    reply, elapsed = complete(provider, model, route.provider, prompt, settings.reply_prefix, request_id=request_id)
    return f"{reply}\n\n🕛模型:{model.id}; 耗时{int(elapsed + 0.5)} 秒"


def complete(provider: Provider, model: Model, provider_id: str, prompt: str, system_prompt: str,
             *, request_id: str | None = None, log_bodies: bool = True) -> tuple[str, float]:
    key = provider.api_key.get_secret_value().strip()
    if not key or not model.id.strip():
        raise ChatError("请先在本地配置 API Key 和模型 ID")
    if provider.base_url.scheme != "https" or provider.base_url.username or provider.base_url.password or provider.base_url.query or provider.base_url.fragment:
        raise ChatError("模型 API 地址应为 HTTPS Base URL")
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model.id, "messages": messages}
    if model.thinking is not None:
        payload["thinking"] = {"type": model.thinking}
    if provider_id == "doubao" and model.web_search:
        payload["web_search_options"] = {}
    body = json.dumps(payload, ensure_ascii=False)
    endpoint = str(provider.base_url).rstrip("/") + "/chat/completions"
    request = Request(
        endpoint,
        data=body.encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    context = log.bind(request_id=request_id, provider=provider_id, model=model.id)
    context.info("ai_chat_request", msg="正在请求模型", method="POST", url=endpoint,
                 request_bytes=len(request.data), request_body=body.replace(key, "[已隐藏]") if log_bodies else None)
    started = perf_counter()
    try:
        with build_opener(NoRedirect()).open(request, timeout=provider.timeout_seconds) as response:
            data = response.read(256 * 1024 + 1)
        elapsed = perf_counter() - started
        context.info("ai_chat_response", msg="已收到模型响应", status=response.status,
                     elapsed_seconds=round(elapsed, 3), response_bytes=len(data),
                     response_body=data[:256 * 1024].decode("utf-8", errors="replace").replace(key, "[已隐藏]") if log_bodies else None)
        if len(data) > 256 * 1024:
            raise ChatError("模型回复超出大小限制")
        text = json.loads(data)["choices"][0]["message"]["content"]
    except HTTPError as exc:
        with exc:
            try:
                error_body = exc.read(8192).decode("utf-8", errors="replace").replace(key, "[已隐藏]")
            except OSError:
                error_body = ""
        context.warning("ai_chat_http_error", msg="模型接口返回错误", status=exc.code,
                        elapsed_seconds=round(perf_counter() - started, 3), response_body=error_body if log_bodies else None)
        raise ChatError(f"模型接口返回 HTTP {exc.code}") from None
    except (URLError, OSError):
        context.warning("ai_chat_transport_error", msg="模型接口连接失败或超时",
                        elapsed_seconds=round(perf_counter() - started, 3))
        raise ChatError("模型接口连接失败或超时") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ChatError("模型接口返回了无法识别的回复") from None
    if not isinstance(text, str) or not text.strip():
        raise ChatError("模型返回了空回复")
    return text.rstrip(), elapsed
