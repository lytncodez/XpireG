"""AI chat: user -> auth -> company -> intent -> deterministic analytics -> context -> AI -> validation -> reply.

The model never receives database access or the ability to run queries; it only sees the verified context
for the detected intent. Conversations are scoped to (company, user).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIProvider, AIProviderError, AITask
from app.ai.context_builder import INTENT_SECTIONS, build_context, detect_intent
from app.ai.guardrails import AIOutputError, collect_facts, parse_json_object, validate_chat, validate_model
from app.ai.prompts import SYSTEM_RULES, chat_prompt
from app.ai.providers.mock_provider import build_chat
from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models import AIConversation, AIMessage, AuditAction, MessageRole, User
from app.schemas.ai import ChatResponse, ModelChatOutput
from app.services import audit_service
from app.utils.dates import utcnow
from app.utils.pagination import PageParams, paginate

logger = get_logger(__name__)

SAFE_FAILURE_ANSWER = ("I could not produce an answer that is fully supported by ExpireGuard's verified data. "
                       "Please try rephrasing, or check the analytics and expiry pages directly.")


async def get_conversation(db: AsyncSession, user: User, conversation_id: uuid.UUID) -> AIConversation:
    conv = await db.scalar(
        select(AIConversation).where(
            AIConversation.id == conversation_id,
            AIConversation.company_id == user.company_id,
            AIConversation.user_id == user.id,
        )
    )
    if conv is None:
        raise NotFoundError("Conversation not found")
    return conv


async def list_conversations(db: AsyncSession, user: User, params: PageParams) -> tuple[list[AIConversation], int]:
    stmt = (
        select(AIConversation)
        .where(AIConversation.company_id == user.company_id, AIConversation.user_id == user.id)
        .order_by(AIConversation.updated_at.desc())
    )
    return await paginate(db, stmt, params)


async def list_messages(db: AsyncSession, conversation_id: uuid.UUID, limit: int | None = None) -> list[AIMessage]:
    stmt = select(AIMessage).where(AIMessage.conversation_id == conversation_id).order_by(AIMessage.created_at.desc())
    if limit:
        stmt = stmt.limit(limit)
    rows = list((await db.scalars(stmt)).all())
    rows.reverse()
    return rows


async def delete_conversation(db: AsyncSession, user: User, conversation_id: uuid.UUID) -> None:
    conv = await get_conversation(db, user, conversation_id)
    await db.delete(conv)
    await db.commit()


def _grounded_fallback(ctx: dict, intent: str, question: str, facts) -> tuple[ModelChatOutput, bool]:
    try:
        out = validate_model(build_chat(ctx, intent, question), ModelChatOutput)
        if validate_chat(out, ctx, facts).ok:
            return out, True  # type: ignore[return-value]
    except AIOutputError:
        logger.exception("Deterministic chat fallback failed validation")
    return ModelChatOutput(answer=SAFE_FAILURE_ANSWER, evidence=[], insufficient_evidence=True), False


async def chat(
    db: AsyncSession,
    user: User,
    provider: AIProvider,
    message: str,
    conversation_id: uuid.UUID | None = None,
    ip: str | None = None,
) -> ChatResponse:
    question = message.strip()
    if conversation_id:
        conv = await get_conversation(db, user, conversation_id)
    else:
        conv = AIConversation(company_id=user.company_id, user_id=user.id, title=question[:80])
        db.add(conv)
        await db.flush()

    history_rows = await list_messages(db, conv.id, settings.AI_CHAT_HISTORY_MESSAGES)
    history = [{"role": m.role.value, "content": m.content} for m in history_rows]

    intent = detect_intent(question)
    ctx = await build_context(db, user.company_id, sections=INTENT_SECTIONS[intent], intent=intent)
    facts = collect_facts(ctx)  # numbers typed by the user are NOT treated as verified facts

    provider_name, model_name = provider.name, provider.model
    fallback_used = False
    violations: list[str] = []
    try:
        response = await provider.generate_response(
            ctx, chat_prompt(intent, question), system=SYSTEM_RULES, task=AITask.CHAT, history=history
        )
        provider_name, model_name = response.provider, response.model
        out: ModelChatOutput = validate_model(parse_json_object(response.text), ModelChatOutput)  # type: ignore[assignment]
        result = validate_chat(out, ctx, facts)
        if not result.ok:
            violations = result.violations[:10]
            raise AIOutputError("Guardrail violations: " + "; ".join(violations[:3]))
        grounded = True
    except (AIProviderError, AIOutputError) as exc:
        logger.warning("Chat answer replaced by grounded fallback: %s", exc)
        violations = violations or [str(exc)[:300]]
        out, grounded = _grounded_fallback(ctx, intent, question, facts)
        fallback_used = True

    db.add(AIMessage(conversation_id=conv.id, role=MessageRole.USER, content=question))
    await db.flush()
    assistant = AIMessage(
        conversation_id=conv.id,
        role=MessageRole.ASSISTANT,
        content=out.answer,
        meta={
            "intent": intent,
            "evidence": [e.model_dump() for e in out.evidence],
            "insufficient_evidence": out.insufficient_evidence,
            "provider": provider_name,
            "model": model_name,
            "fallback_used": fallback_used,
            "guardrail_violations": violations,
            "as_of": ctx["business"]["as_of"],
        },
    )
    db.add(assistant)
    conv.updated_at = utcnow()
    audit_service.record(
        db, action=AuditAction.AI_CHAT_MESSAGE, company_id=user.company_id, user_id=user.id,
        entity_type="ai_conversation", entity_id=conv.id,
        metadata={"intent": intent, "provider": provider_name, "fallback_used": fallback_used}, ip_address=ip,
    )
    await db.commit()
    await db.refresh(assistant)
    return ChatResponse(
        conversation_id=conv.id,
        message_id=assistant.id,
        intent=intent,
        answer=out.answer,
        evidence=out.evidence,
        insufficient_evidence=out.insufficient_evidence,
        follow_up_suggestions=out.follow_up_suggestions,
        grounded=grounded,
        fallback_used=fallback_used,
        provider=provider_name,
        model=model_name,
    )
