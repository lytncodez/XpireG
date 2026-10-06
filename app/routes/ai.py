import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.ai.base import AIProvider
from app.ai.context_builder import INTENT_SECTIONS, build_context
from app.ai.conversation import chat, delete_conversation, get_conversation, list_conversations, list_messages
from app.ai.factory import get_ai_provider, get_provider_selection
from app.core.config import settings
from app.core.exceptions import error_responses
from app.dependencies.auth import ManagerUser, StaffUser, rate_limit
from app.dependencies.database import DbSession
from app.schemas import Page
from app.schemas.ai import AIStatus, ChatRequest, ChatResponse, ConversationDetail, ConversationRead, MessageRead
from app.utils.helpers import client_ip
from app.utils.pagination import PageParams, page_meta, page_params

router = APIRouter(prefix="/ai", tags=["AI chat"])


@router.post(
    "/chat", response_model=ChatResponse, summary="Ask the AI assistant",
    description="Flow: authentication → company → deterministic intent detection → analytics for that intent → "
                "verified context → AI provider → schema validation → guardrails → stored reply. The AI never "
                "receives database access and cannot run queries. If its answer fails validation, a deterministic "
                "grounded answer is returned (fallback_used=true). Omit conversation_id to start a new conversation.",
    responses=error_responses(401, 404, 422, 429),
    dependencies=[Depends(rate_limit("ai", settings.AI_RATE_LIMIT_PER_MINUTE))],
)
async def ai_chat(
    data: ChatRequest, user: StaffUser, request: Request, db: DbSession,
    provider: AIProvider = Depends(get_ai_provider),
) -> ChatResponse:
    return await chat(db, user, provider, data.message, data.conversation_id, client_ip(request))


@router.get("/conversations", response_model=Page[ConversationRead], summary="My conversations",
            description="Your own conversations, most recent first.", responses=error_responses(401))
async def conversations(user: StaffUser, db: DbSession, params: PageParams = Depends(page_params)) -> Page[ConversationRead]:
    items, total = await list_conversations(db, user, params)
    return Page(items=[ConversationRead.model_validate(c) for c in items], **page_meta(total, params))


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail, summary="Conversation with messages",
            description="Only the owner can read a conversation.", responses=error_responses(401, 404))
async def conversation_detail(conversation_id: uuid.UUID, user: StaffUser, db: DbSession) -> ConversationDetail:
    conv = await get_conversation(db, user, conversation_id)
    messages = await list_messages(db, conv.id)
    return ConversationDetail(
        id=conv.id, title=conv.title, created_at=conv.created_at, updated_at=conv.updated_at,
        messages=[MessageRead.model_validate(m) for m in messages],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete conversation", description="Deletes one of your conversations and its messages.",
               responses=error_responses(401, 404))
async def remove_conversation(conversation_id: uuid.UUID, user: StaffUser, db: DbSession) -> Response:
    await delete_conversation(db, user, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/status", response_model=AIStatus, summary="AI provider status",
            description="Which provider and model are active. Never returns credentials.", responses=error_responses(401))
async def ai_status(user: StaffUser) -> AIStatus:
    sel = get_provider_selection()
    return AIStatus(provider=sel.provider.name, model=sel.provider.model, is_mock=sel.provider.name == "mock",
                    configured_provider=sel.configured, fallback_reason=sel.fallback_reason)


@router.get("/context", summary="Inspect the verified AI context",
            description="Returns exactly what the AI would receive for an intent, for transparency and auditing. "
                        "MANAGER or ADMIN.", responses=error_responses(401, 403, 422))
async def ai_context(
    user: ManagerUser, db: DbSession,
    intent: str = Query("GENERAL", pattern="^(" + "|".join(INTENT_SECTIONS) + ")$"),
    period_days: int = Query(30, ge=7, le=365),
) -> dict[str, Any]:
    return await build_context(db, user.company_id, sections=INTENT_SECTIONS[intent], intent=intent, period_days=period_days)
