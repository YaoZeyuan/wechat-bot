"""Desktop message-source setup endpoints."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from .ciphertalk import CipherTalk, SourceError
from .ciphertalk_cli import CipherTalkCLI

router = APIRouter(prefix="/api/reader", tags=["desktop reader"])
log = logging.getLogger("uvicorn.error")


class SourceSettings(BaseModel):
    port: int | None = Field(default=None, ge=1, le=65535)
    token: SecretStr = SecretStr("")
    query: str = Field(default="", max_length=100)
    session_id: str | None = Field(default=None, max_length=256)


@router.post("/probe")
def probe(settings: SourceSettings) -> dict:
    """Check a source without exposing its token or returning chat content."""
    try:
        if settings.port is None:
            source = CipherTalkCLI()
            source.check_connection()
        else:
            source = CipherTalk(settings.port, settings.token.get_secret_value())
        result = source.sessions(q=settings.query, limit=50)
        groups = [
            {"id": item["sessionId"], "name": item["displayName"]}
            for item in result["items"]
            if item["kind"] == "group"
        ]
        message_readable = None
        if settings.session_id:
            if not settings.session_id.endswith("@chatroom"):
                raise HTTPException(422, "请选择微信群")
            source.messages(settings.session_id, limit=1, includeRaw=False, includeMediaPaths=False)
            message_readable = True
    except SourceError as exc:
        log.warning("Desktop reader probe failed: %s", exc)
        raise HTTPException(502, str(exc)) from None
    except (KeyError, TypeError):
        log.warning("Desktop reader received an unexpected CipherTalk response")
        raise HTTPException(502, "密语代理返回了不兼容的会话数据") from None
    log.info("Desktop reader probe succeeded; groups=%d, message_readable=%s", len(groups), message_readable)
    return {"groups": groups, "has_more": bool(result.get("hasMore")), "message_readable": message_readable}
