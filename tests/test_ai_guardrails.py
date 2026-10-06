import pytest

from app.ai.guardrails import (
    AIOutputError,
    check_text,
    collect_facts,
    parse_json_object,
    validate_chat,
    validate_insight,
    validate_model,
)
from app.schemas.ai import ModelChatOutput
from app.schemas.insights import ModelInsightOutput

PID = "11111111-1111-1111-1111-111111111111"
CTX = {
    "business": {"currency": "KES", "as_of": "2026-10-04"},
    "sales": {"revenue": 12450.0, "previous_revenue": 13561, "revenue_growth_pct": -8.19},
    "expiry_risks": [{"product_id": PID, "product": "Natural Yoghurt 500g", "batch_number": "Y-1",
                      "expiry_date": "2026-10-12", "days_remaining": 8, "units_at_risk": 230}],
}
FACTS = collect_facts(CTX)


def insight(**over):
    data = {
        "title": "Natural Yoghurt 500g batch Y-1 expires in 8 days",
        "summary": "230 units are at risk before 2026-10-12.",
        "explanation": "Projected from recent sales velocity.",
        "severity": "CRITICAL", "category": "EXPIRY",
        "supporting_evidence": [{"label": "Units at risk", "value": 230, "source": "expiry_risks[0].units_at_risk"}],
        "recommendation": "Prioritise FEFO movement for this batch.",
        "product_id": PID, "batch_id": None,
    }
    data.update(over)
    return validate_model(data, ModelInsightOutput)


def test_grounded_insight_passes():
    result = validate_insight(insight(), CTX, FACTS)
    assert result.ok, result.violations
    assert result.checked_numbers >= 3


def test_rounding_and_sign_are_tolerated():
    assert check_text(["Revenue was 12,450 and fell 8.2% (previously 13,561)."], FACTS)[0] == []


@pytest.mark.parametrize("text", [
    "Revenue will reach 99,999 next month.",
    "Sales fell 12%.",
    "The batch expires on 2026-11-01.",
])
def test_invented_numbers_and_dates_rejected(text):
    assert not validate_insight(insight(summary=text), CTX, FACTS).ok


def test_evidence_must_match_context():
    bad_value = insight(supporting_evidence=[{"label": "Units", "value": 999, "source": "expiry_risks[0].units_at_risk"}])
    assert any("does not match" in v for v in validate_insight(bad_value, CTX, FACTS).violations)
    bad_path = insight(supporting_evidence=[{"label": "Units", "value": 230, "source": "expiry_risks[5].units_at_risk"}])
    assert any("does not exist" in v for v in validate_insight(bad_path, CTX, FACTS).violations)


def test_unknown_product_id_rejected():
    out = insight(product_id="99999999-9999-9999-9999-999999999999")
    assert any("Unknown id" in v for v in validate_insight(out, CTX, FACTS).violations)


def test_causation_claims_rejected_unless_hedged():
    assert not validate_insight(insight(explanation="Inflation caused the sales decline."), CTX, FACTS).ok
    assert not validate_insight(insight(explanation="Sales dropped because of competitors."), CTX, FACTS).ok
    hedged = ("Sales declined during the same period. This may be a contributing factor, but the available data "
              "does not establish causation.")
    assert validate_insight(insight(explanation=hedged), CTX, FACTS).ok


def test_claims_of_automatic_action_rejected():
    assert not validate_insight(insight(recommendation="I have discounted the batch."), CTX, FACTS).ok
    assert not validate_insight(insight(recommendation="The stock has been reordered automatically."), CTX, FACTS).ok


def test_schema_validation_of_malformed_output():
    with pytest.raises(AIOutputError):
        validate_model({"title": "x"}, ModelInsightOutput)
    with pytest.raises(AIOutputError):
        insight(supporting_evidence=[])  # evidence is mandatory
    with pytest.raises(AIOutputError):
        insight(category="WEATHER")
    with pytest.raises(AIOutputError):
        validate_model({"answer": "hi", "unexpected": 1}, ModelChatOutput)  # extra keys forbidden


@pytest.mark.parametrize("raw", ["", "not json at all", "[1, 2]", '{"answer": "unterminated'])
def test_unparseable_responses(raw):
    with pytest.raises(AIOutputError):
        parse_json_object(raw)


def test_code_fenced_json_is_accepted():
    assert parse_json_object('```json\n{"answer": "ok"}\n```') == {"answer": "ok"}


def test_chat_answer_cannot_invent_data():
    ok = validate_model({"answer": "Natural Yoghurt 500g has 230 units at risk, expiring in 8 days."}, ModelChatOutput)
    assert validate_chat(ok, CTX, FACTS).ok
    invented = validate_model({"answer": "Natural Yoghurt 500g will sell 400 units next week."}, ModelChatOutput)
    assert not validate_chat(invented, CTX, FACTS).ok
