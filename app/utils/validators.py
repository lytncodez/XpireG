"""Reusable input normalisation and validation."""

from __future__ import annotations

import re

_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_BARCODE_RE = re.compile(r"^[A-Za-z0-9\-\.]{1,64}$")
_SKU_RE = re.compile(r"^[A-Za-z0-9\-_\./]{1,64}$")


def normalize_phone(value: str | None) -> str | None:
    """Normalise to E.164 (+<country><number>). Spaces, dashes and brackets are removed."""
    if value is None:
        return None
    cleaned = re.sub(r"[\s\-\(\)]", "", value.strip())
    if not cleaned:
        return None
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not _PHONE_RE.match(cleaned):
        raise ValueError("Phone number must be in international format, e.g. +254712345678")
    return cleaned


def normalize_barcode(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", "", str(value))
    if cleaned.endswith(".0") and cleaned[:-2].isdigit():  # spreadsheet float artefact
        cleaned = cleaned[:-2]
    if not cleaned:
        return None
    if not _BARCODE_RE.match(cleaned):
        raise ValueError("Barcode may contain only letters, digits, '-' and '.' (max 64 chars)")
    return cleaned


def normalize_sku(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip().upper()
    if not cleaned:
        return None
    if not _SKU_RE.match(cleaned):
        raise ValueError("SKU may contain only letters, digits and - _ . / (max 64 chars)")
    return cleaned


def normalize_batch_number(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip().upper()
    if cleaned.endswith(".0") and cleaned[:-2].isdigit():
        cleaned = cleaned[:-2]
    return cleaned or None


def validate_password_strength(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if len(password) > 128:
        raise ValueError("Password must be at most 128 characters long")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("Password must contain at least one letter and one digit")
    return password


def require_batch_number(value: str) -> str:
    normalized = normalize_batch_number(value)
    if not normalized:
        raise ValueError("Batch number is required")
    return normalized
