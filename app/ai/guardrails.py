"""Guardrails applied to every AI output before it is stored or returned.

1. parse:      the reply must be a single JSON object (code fences tolerated) matching a Pydantic schema.
2. numbers:    every number in the text must exist in the verified context (rounding to the precision
               written is allowed; sign is ignored so "fell 8.2%" matches -8.2).
3. dates:      every ISO date must exist in the context.
4. evidence:   each evidence item's "source" path must resolve in the context and its value must match.
5. ids:        product_id / batch_id must appear in the context.
6. causation:  sentences about sales/demand changes, or naming external factors (inflation, competitors,
               weather...), must be hedged; unhedged causal claims are rejected.
7. autonomy:   text must not claim that an action was performed.

Pure functions only (no I/O) so they are easy to test.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
LIST_MARKER_RE = re.compile(r"(?m)^\s*\d{1,2}[.)]\s+")
NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")
PATH_TOKEN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")
ALLOWED_CONSTANTS = {0.0, 100.0}
MAX_PRECISION = 4

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
CAUSAL_RE = re.compile(
    r"\b(caused|causes|causing|because of|due to(?! expire)|led to|leads to|resulted in|results in|"
    r"drove|driven by|is the reason|was the reason|responsible for|attributable to)\b",
    re.IGNORECASE,
)
CHANGE_TERMS_RE = re.compile(r"\b(sales|revenue|demand|decline|declined|increase|increased|drop|dropped|growth|spike|fell|rose)\b", re.IGNORECASE)
EXTERNAL_FACTOR_RE = re.compile(
    r"\b(inflation|competitor|competitors|competition|weather|season|seasonal|holiday|holidays|economy|economic|"
    r"pandemic|strike|price war|marketing campaign)\b",
    re.IGNORECASE,
)
HEDGE_RE = re.compile(
    r"\b(may|might|could|possibl\w*|perhaps|not establish\w*|does not prove|cannot be determined|"
    r"correlat\w*|coincid\w*|unclear|unknown|not known|no evidence|insufficient)\b",
    re.IGNORECASE,
)
AUTONOMY_RE = re.compile(
    r"\b(i have|i've|i (?:ordered|discounted|removed|placed|updated|changed|scheduled)|"
    r"we have (?:ordered|discounted|removed|placed|changed)|"
    r"has been (?:ordered|discounted|removed|reordered|placed|marked down|scheduled)|"
    r"have been (?:ordered|discounted|removed|reordered|placed|marked down|scheduled)|"
    r"automatically (?:ordered|discounted|removed|applied|placed|reordered))\b",
    re.IGNORECASE,
)


class AIOutputError(Exception):
    """The model output is not valid JSON / does not match the schema."""


@dataclass
class ContextFacts:
    rounded: dict[int, set[float]] = field(default_factory=lambda: {d: set() for d in range(MAX_PRECISION + 1)})
    strings: set[str] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    ids: set[str] = field(default_factory=set)

    def add_number(self, value: float) -> None:
        v = abs(float(value))
        for d in self.rounded:
            self.rounded[d].add(round(v, d))

    def supports(self, value: float, precision: int) -> bool:
        v = abs(value)
        if v in ALLOWED_CONSTANTS:
            return True
        d = min(precision, MAX_PRECISION)
        return round(v, d) in self.rounded[d]


@dataclass
class GuardrailResult:
    ok: bool
    violations: list[str]
    checked_numbers: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.ok, "violations": self.violations, "checked_numbers": self.checked_numbers}


# --------------------------------------------------------------------------- fact collection


def _numbers_in_text(text: str) -> list[tuple[float, int, str]]:
    """Return (value, decimals, raw) for every number in free text (UUIDs/dates/list markers removed)."""
    cleaned = UUID_RE.sub(" ", text)
    cleaned = DATE_RE.sub(" ", cleaned)
    cleaned = LIST_MARKER_RE.sub(" ", cleaned)
    out = []
    for m in NUMBER_RE.finditer(cleaned):
        whole, frac = m.group(1), m.group(2)
        raw = whole + (f".{frac}" if frac else "")
        out.append((float(whole.replace(",", "") + (f".{frac}" if frac else "")), len(frac) if frac else 0, raw))
    return out


def collect_facts(*sources: Any, extra_text: str | None = None) -> ContextFacts:
    facts = ContextFacts()

    def walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            facts.add_number(node)
        elif isinstance(node, str):
            facts.strings.add(node.strip().lower())
            for d in DATE_RE.findall(node):
                facts.dates.add(d)
            for u in UUID_RE.findall(node):
                facts.ids.add(u.lower())
            for value, _, _ in _numbers_in_text(node):
                facts.add_number(value)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple, set)):
            for v in node:
                walk(v)
        else:
            walk(str(node))

    for src in sources:
        walk(src)
    if extra_text:
        for value, _, _ in _numbers_in_text(extra_text):
            facts.add_number(value)
        facts.dates.update(DATE_RE.findall(extra_text))
    return facts


# --------------------------------------------------------------------------- parsing


def parse_json_object(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise AIOutputError("Empty model response")
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", body)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise AIOutputError("Model response is not a JSON object")
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AIOutputError(f"Model response is not valid JSON ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise AIOutputError("Model response is not a JSON object")
    return data


def validate_model(data: Any, model_cls: type[BaseModel]) -> BaseModel:
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise AIOutputError(f"Schema validation failed: {problems}") from exc


# --------------------------------------------------------------------------- checks


def resolve_path(root: Any, path: str) -> tuple[bool, Any]:
    node = root
    tokens = PATH_TOKEN_RE.findall(path.strip())
    if not tokens:
        return False, None
    for name, index in tokens:
        if name:
            if not isinstance(node, dict) or name not in node:
                return False, None
            node = node[name]
        else:
            i = int(index)
            if not isinstance(node, list) or i >= len(node):
                return False, None
            node = node[i]
    return True, node


def _values_match(claimed: Any, actual: Any) -> bool:
    if claimed is None or isinstance(claimed, bool) or isinstance(actual, bool) or actual is None:
        return claimed == actual
    if isinstance(actual, (int, float)):
        try:
            c = float(str(claimed).replace(",", "").rstrip("%"))
        except ValueError:
            return False
        text = str(claimed)
        decimals = len(text.split(".")[1].rstrip("%")) if "." in text else 0
        return round(abs(c), min(decimals, MAX_PRECISION)) == round(abs(float(actual)), min(decimals, MAX_PRECISION)) \
            or abs(c - float(actual)) < 1e-6
    return str(claimed).strip().lower() == str(actual).strip().lower()


def check_text(texts: list[str], facts: ContextFacts) -> tuple[list[str], int]:
    violations: list[str] = []
    checked = 0
    for text in texts:
        if not text:
            continue
        for value, decimals, raw in _numbers_in_text(text):
            checked += 1
            if not facts.supports(value, decimals):
                violations.append(f"Unsupported number '{raw}' (not present in verified context)")
        for d in DATE_RE.findall(text):
            if d not in facts.dates:
                violations.append(f"Unsupported date '{d}'")
        for sentence in SENTENCE_SPLIT_RE.split(text):
            if not sentence.strip():
                continue
            causal = CAUSAL_RE.search(sentence) and CHANGE_TERMS_RE.search(sentence)
            external = EXTERNAL_FACTOR_RE.search(sentence)
            if (causal or external) and not HEDGE_RE.search(sentence):
                violations.append(f"Unhedged causal claim: '{sentence.strip()[:160]}'")
            if AUTONOMY_RE.search(sentence):
                violations.append(f"Claims an action was taken: '{sentence.strip()[:160]}'")
    return list(dict.fromkeys(violations)), checked


def check_evidence(items: list[Any], root: dict[str, Any], *, required: bool) -> list[str]:
    violations: list[str] = []
    if required and not items:
        violations.append("No supporting evidence supplied")
    for item in items:
        found, actual = resolve_path(root, item.source)
        if not found:
            violations.append(f"Evidence source '{item.source}' does not exist in the verified context")
        elif isinstance(actual, (dict, list)):
            violations.append(f"Evidence source '{item.source}' must point to a single value")
        elif not _values_match(item.value, actual):
            violations.append(f"Evidence '{item.label}' value {item.value!r} does not match context ({actual!r})")
    return violations


def check_ids(ids: list[Any], facts: ContextFacts) -> list[str]:
    return [f"Unknown id '{i}' (not in verified context)" for i in ids if i is not None and str(i).lower() not in facts.ids]


def validate_insight_like(out: Any, root: dict[str, Any], facts: ContextFacts, text_fields: list[str],
                          evidence_field: str = "supporting_evidence") -> GuardrailResult:
    texts = [getattr(out, f) for f in text_fields]
    text_violations, checked = check_text(texts, facts)
    violations = text_violations
    violations += check_evidence(getattr(out, evidence_field), root, required=True)
    violations += check_ids([getattr(out, "product_id", None), getattr(out, "batch_id", None)], facts)
    return GuardrailResult(ok=not violations, violations=violations, checked_numbers=checked)


def validate_insight(out: Any, root: dict[str, Any], facts: ContextFacts) -> GuardrailResult:
    return validate_insight_like(out, root, facts, ["title", "summary", "explanation", "recommendation"])


def validate_recommendation(out: Any, root: dict[str, Any], facts: ContextFacts) -> GuardrailResult:
    return validate_insight_like(out, root, facts, ["title", "action", "rationale"])


def validate_chat(out: Any, root: dict[str, Any], facts: ContextFacts) -> GuardrailResult:
    texts = [out.answer, *out.follow_up_suggestions]
    violations, checked = check_text(texts, facts)
    violations += check_evidence(out.evidence, root, required=False)
    return GuardrailResult(ok=not violations, violations=violations, checked_numbers=checked)
