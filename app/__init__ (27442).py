"""ExpireGuard AI intelligence layer (Phase 2).

Pipeline (the LLM never touches the database and never computes business numbers):

    PostgreSQL -> deterministic analytics -> pattern & anomaly detection -> context_builder
    -> AI provider (mock | openai | anthropic) -> Pydantic validation -> guardrails
    -> stored insight / recommendation / chat answer
"""
