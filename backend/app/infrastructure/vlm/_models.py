from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class _ChatMessageContentText(BaseModel):
    type: Literal["text"]
    text: str


class _ChatMessageContentImageUrl(BaseModel):
    type: Literal["image_url"]
    image_url: dict[str, str]


class _ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: list[_ChatMessageContentText | _ChatMessageContentImageUrl] | str


class _ChatCompletionRequest(BaseModel):
    model: str
    messages: list[_ChatMessage]


class _ChatCompletionMessage(BaseModel):
    role: str
    content: str | None = None
    reasoning_content: str | None = None


class _ChatCompletionChoice(BaseModel):
    index: int
    message: _ChatCompletionMessage


class _ChatCompletionResponse(BaseModel):
    choices: list[_ChatCompletionChoice]
