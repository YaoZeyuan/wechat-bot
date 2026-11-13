"""Versioned message and reply contract shared by plugins and HTTP hooks."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MessageAuthor(ContractModel):
    user_id: NonEmpty
    group_nickname: str | None = None
    username: str | None = None
    remark: str | None = None


class MessageEvent(ContractModel):
    version: Literal[1] = 1
    group_id: NonEmpty
    message_id: NonEmpty
    sender_id: NonEmpty
    author: MessageAuthor | None = None
    timestamp_ms: int = Field(gt=0)
    text: str


class Text(ContractModel):
    type: Literal["text"] = "text"
    text: NonEmpty


class Attachment(ContractModel):
    type: Literal["image", "txt", "pdf"]
    path: NonEmpty


class WebPage(ContractModel):
    type: Literal["webpage"] = "webpage"
    url: HttpUrl


class Quote(ContractModel):
    message_id: NonEmpty
    sender_id: NonEmpty
    timestamp_ms: int = Field(gt=0)
    excerpt: str = Field(max_length=240)

    @classmethod
    def from_event(cls, event: MessageEvent):
        return cls(
            message_id=event.message_id, sender_id=event.sender_id,
            timestamp_ms=event.timestamp_ms, excerpt=event.text[:240],
        )


class Mention(ContractModel):
    member_id: NonEmpty
    display_name: NonEmpty


class Reply(ContractModel):
    content: Annotated[Text | Attachment | WebPage, Field(discriminator="type")]
    quote: Quote | None = None
    mentions: tuple[Mention, ...] = ()


class SendTask(ContractModel):
    version: Literal[1] = 1
    group_id: NonEmpty
    trigger_message_id: NonEmpty
    reply: Reply

    @classmethod
    def from_event(cls, event: MessageEvent, reply: Reply):
        return cls(group_id=event.group_id, trigger_message_id=event.message_id, reply=reply)
