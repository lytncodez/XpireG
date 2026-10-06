"""AI chat contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.ai_conversation import MessageRole
from app.schemas import ORMModel
from app.schemas.insights import EvidenceItem


class ModelChatOutput(BaseModel):
    """What the LLM must return for a chat turn."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=6000)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=30)
    insufficient_evidence: bool = False
    follow_up_suggestions: list[str] = Field(default_factory=list, max_length=5)


class ChatRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"message": "Which products are expiring soon?"}]})

    message: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = Field(default=None, description="Omit to start a new conversation")


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    intent: str
    answer: str
    evidence: list[EvidenceItem]
    insufficient_evidence: bool
    follow_up_suggestions: list[str]
    grounded: bool = Field(description="Every number in the answer was verified against the supplied context")
    fallback_used: bool = Field(description="AI output failed validation; a deterministic grounded answer was returned")
    provider: str
    model: str


class MessageRead(ORMModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="meta")
    created_at: datetime


class ConversationRead(ORMModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead]


class AIStatus(BaseModel):
    provider: str
    model: str
    is_mock: bool
    configured_provider: str
    fallback_reason: str | None = Field(description="Why the mock provider is used instead of the configured one")
