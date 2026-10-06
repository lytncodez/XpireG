"""Reusable annotated field types (normalisation runs after basic type validation)."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, StringConstraints

from app.utils.validators import (
    normalize_barcode,
    normalize_phone,
    normalize_sku,
    require_batch_number,
    validate_password_strength,
)

OptionalPhone = Annotated[str | None, AfterValidator(normalize_phone)]
Phone = Annotated[str, AfterValidator(normalize_phone)]
Password = Annotated[str, AfterValidator(validate_password_strength)]
OptionalSKU = Annotated[str | None, AfterValidator(normalize_sku)]
OptionalBarcode = Annotated[str | None, AfterValidator(normalize_barcode)]
BatchNumber = Annotated[str, StringConstraints(min_length=1, max_length=64), AfterValidator(require_batch_number)]
