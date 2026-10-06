"""Tabular import pipeline shared by CSV and Excel uploads.

Stages:
  1. read      -> pandas DataFrame (csv_service.read_csv / excel_service.read_excel)
  2. normalise -> canonical column names via aliases
  3. validate  -> per-row parsing; errors collected with spreadsheet row numbers
  4. persist   -> one transaction for all valid rows (any DB failure rolls back everything)
  5. expiry    -> new batches are classified and alerted immediately

The parsing half (stages 2-3) is pure Python so it can be unit tested without a database.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from app.utils.validators import normalize_barcode, normalize_batch_number, normalize_sku

# --------------------------------------------------------------------------- column mapping

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "product_name": ("product_name", "product", "name", "item", "item_name", "description_name"),
    "sku": ("sku", "product_code", "item_code", "code"),
    "barcode": ("barcode", "ean", "upc", "gtin", "bar_code"),
    "category": ("category", "category_name", "department"),
    "brand": ("brand", "manufacturer"),
    "unit": ("unit", "uom", "unit_of_measure"),
    "description": ("description", "details"),
    "selling_price": ("selling_price", "price", "retail_price", "unit_price", "sale_price"),
    "cost_price": ("cost_price", "cost", "purchase_price", "buying_price"),
    "batch_number": ("batch_number", "batch", "batch_no", "lot", "lot_number", "lot_no"),
    "manufacturing_date": ("manufacturing_date", "mfg_date", "manufactured", "production_date", "mfd"),
    "expiry_date": ("expiry_date", "expiration_date", "exp_date", "expiry", "best_before", "use_by"),
    "quantity": ("quantity", "qty", "stock", "units", "quantity_received"),
}
REQUIRED_COLUMNS = ("product_name", "batch_number", "expiry_date", "quantity")
IDENTIFIER_COLUMNS = ("sku", "barcode")
MAX_ERRORS_STORED = 5000

_STRING_DATE_FORMATS_DAYFIRST = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%y", "%d %b %Y", "%d %B %Y")
_STRING_DATE_FORMATS_MONTHFIRST = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%m/%d/%y", "%b %d %Y", "%B %d %Y")


class ImportFileError(Exception):
    """The file as a whole is unusable (unreadable, wrong columns, too many rows)."""

    def __init__(self, message: str, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []


def _canonical(header: Any) -> str:
    text = re.sub(r"[^0-9a-zA-Z]+", "_", str(header).strip().lower()).strip("_")
    return text


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    lookup = {alias: canonical for canonical, aliases in COLUMN_ALIASES.items() for alias in aliases}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for col in df.columns:
        canonical = lookup.get(_canonical(col))
        if canonical and canonical not in used:
            mapping[col] = canonical
            used.add(canonical)
    df = df[list(mapping.keys())].rename(columns=mapping)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    problems = [{"row": None, "field": c, "value": None, "error_type": "FILE", "message": f"Missing required column '{c}'"} for c in missing]
    if not any(c in df.columns for c in IDENTIFIER_COLUMNS):
        problems.append({"row": None, "field": "sku/barcode", "value": None, "error_type": "FILE",
                         "message": "File needs a 'sku' or 'barcode' column to identify products"})
    if problems:
        raise ImportFileError("The file is missing required columns", problems)
    return df


# --------------------------------------------------------------------------- cell parsing


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if value is pd.NaT:
        return True
    return isinstance(value, str) and value.strip() == ""


def clean_str(value: Any, max_len: int | None = None) -> str | None:
    if is_blank(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if max_len and len(text) > max_len:
        raise ValueError(f"must be at most {max_len} characters")
    return text


def parse_date(value: Any, dayfirst: bool = True) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        serial = float(value)
        if 20000 <= serial <= 80000:  # Excel serial date (1954..2119)
            return date(1899, 12, 30) + timedelta(days=int(serial))
        raise ValueError("is not a valid date")
    text = str(value).strip()
    text = text.split("T")[0] if re.match(r"^\d{4}-\d{2}-\d{2}T", text) else text
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text):
        text = text[:10]
    formats = _STRING_DATE_FORMATS_DAYFIRST if dayfirst else _STRING_DATE_FORMATS_MONTHFIRST
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("is not a recognised date (use YYYY-MM-DD)")


def parse_quantity(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("must be a whole number")
    if isinstance(value, (int,)):
        qty = value
    else:
        text = str(value).strip().replace(",", "")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("must be a whole number") from exc
        if number != number.to_integral_value():
            raise ValueError("must be a whole number")
        qty = int(number)
    if qty <= 0:
        raise ValueError("must be greater than zero")
    if qty > 1_000_000_000:
        raise ValueError("is unrealistically large")
    return qty


def parse_price(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("must be a number")
    text = re.sub(r"[^\d.\-]", "", str(value).strip())
    try:
        price = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("must be a number") from exc
    if not price.is_finite() or price < 0:
        raise ValueError("must be zero or positive")
    if price > Decimal("9999999999.99"):
        raise ValueError("is too large")
    return price.quantize(Decimal("0.01"))


# --------------------------------------------------------------------------- row validation


@dataclass
class ParsedRow:
    row_number: int
    product_name: str
    sku: str | None
    barcode: str | None
    category: str | None
    brand: str | None
    unit: str | None
    description: str | None
    selling_price: Decimal | None
    cost_price: Decimal | None
    batch_number: str
    manufacturing_date: date | None
    expiry_date: date
    quantity: int

    @property
    def identity_key(self) -> str:
        return f"sku:{self.sku}" if self.sku else f"barcode:{self.barcode}"


@dataclass
class ParseResult:
    total_rows: int
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    failed_row_numbers: set[int] = field(default_factory=set)
    duplicate_rows: int = 0


def _err(result: ParseResult, row: int | None, fld: str | None, value: Any, kind: str, message: str) -> None:
    if len(result.errors) < MAX_ERRORS_STORED:
        safe_value = None if is_blank(value) else str(value)[:200]
        result.errors.append({"row": row, "field": fld, "value": safe_value, "error_type": kind, "message": message})


def validate_dataframe(df: pd.DataFrame, *, dayfirst: bool = True) -> ParseResult:
    """Validate and normalise every row. Pure function: no I/O."""
    df = normalize_columns(df)
    result = ParseResult(total_rows=len(df))
    seen: dict[tuple[str, str], int] = {}

    for idx, record in zip(df.index, df.to_dict(orient="records")):
        row_no = int(idx) + 2  # header is row 1; index follows the sheet even if rows were dropped
        if all(is_blank(v) for v in record.values()):
            result.total_rows -= 1
            continue
        errors_before = len(result.errors)
        values: dict[str, Any] = {}

        def take(name: str, fn, required: bool = False):
            raw = record.get(name)
            if is_blank(raw):
                if required:
                    _err(result, row_no, name, raw, "MISSING", f"{name} is required")
                    result.failed_row_numbers.add(row_no)
                return None
            try:
                return fn(raw)
            except ValueError as exc:
                _err(result, row_no, name, raw, "INVALID", f"{name} {exc}")
                result.failed_row_numbers.add(row_no)
                return None

        values["product_name"] = take("product_name", lambda v: clean_str(v, 200), required=True)
        values["sku"] = take("sku", lambda v: normalize_sku(clean_str(v)))
        values["barcode"] = take("barcode", lambda v: normalize_barcode(clean_str(v)))
        values["category"] = take("category", lambda v: clean_str(v, 120))
        values["brand"] = take("brand", lambda v: clean_str(v, 120))
        values["unit"] = take("unit", lambda v: clean_str(v, 32))
        values["description"] = take("description", lambda v: clean_str(v, 5000))
        values["selling_price"] = take("selling_price", parse_price)
        values["cost_price"] = take("cost_price", parse_price)
        values["batch_number"] = take("batch_number", lambda v: normalize_batch_number(clean_str(v, 64)), required=True)
        values["manufacturing_date"] = take("manufacturing_date", lambda v: parse_date(v, dayfirst))
        values["expiry_date"] = take("expiry_date", lambda v: parse_date(v, dayfirst), required=True)
        values["quantity"] = take("quantity", parse_quantity, required=True)

        if not values["sku"] and not values["barcode"]:
            _err(result, row_no, "sku/barcode", None, "MISSING", "Each row needs a sku or a barcode")
            result.failed_row_numbers.add(row_no)
        mfg, exp = values["manufacturing_date"], values["expiry_date"]
        if mfg and exp and exp < mfg:
            _err(result, row_no, "expiry_date", exp.isoformat(), "INVALID", "expiry_date cannot be before manufacturing_date")
            result.failed_row_numbers.add(row_no)
        if mfg and mfg > date.today() + timedelta(days=1):
            _err(result, row_no, "manufacturing_date", mfg.isoformat(), "INVALID", "manufacturing_date cannot be in the future")
            result.failed_row_numbers.add(row_no)

        if len(result.errors) > errors_before or row_no in result.failed_row_numbers:
            continue

        parsed = ParsedRow(row_number=row_no, **values)
        key = (parsed.identity_key, parsed.batch_number)
        if key in seen:
            result.duplicate_rows += 1
            _err(result, row_no, "batch_number", parsed.batch_number, "DUPLICATE",
                 f"Duplicate of row {seen[key]} (same product and batch number)")
            continue
        seen[key] = row_no
        result.rows.append(parsed)
    return result


# --------------------------------------------------------------------------- readers


def read_csv(content: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 always decodes
        raise ImportFileError("Could not decode the CSV file")
    try:
        df = pd.read_csv(
            io.StringIO(text), dtype=str, keep_default_na=False, sep=None, engine="python", skip_blank_lines=False
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError, csv.Error) as exc:
        raise ImportFileError(f"Could not parse CSV: {exc}") from exc
    if df.empty and not len(df.columns):
        raise ImportFileError("The CSV file is empty")
    return df


# --------------------------------------------------------------------------- persistence


async def process_import(
    db,
    user,
    *,
    filename: str,
    content: bytes,
    file_type,
    strict: bool = False,
    ip: str | None = None,
):
    """Run the full import. Returns ImportResult. Never leaves a partially written import.

    DB-layer imports are local so the parsing half of this module stays importable without a database.
    """
    from app.core.config import settings
    from app.core.logging import get_logger
    from app.models import AuditAction, Batch, ImportFileType, ImportJob, ImportStatus, Product
    from app.schemas.imports import ImportResult, ImportRowError
    from app.services import audit_service, product_service
    from app.services.auth_service import get_company
    from app.services.batch_service import build_batch
    from app.services.excel_service import read_excel
    from app.services.expiry_service import run_expiry_check
    from app.utils.dates import today_in_tz, utcnow
    from sqlalchemy import or_, select, tuple_

    logger = get_logger(__name__)
    job = ImportJob(
        company_id=user.company_id, created_by=user.id, filename=filename[:255],
        file_type=file_type, status=ImportStatus.PROCESSING,
    )
    db.add(job)
    await db.flush()
    audit_service.record(
        db, action=AuditAction.IMPORT_STARTED, company_id=user.company_id, user_id=user.id,
        entity_type="import_job", entity_id=job.id, metadata={"filename": filename, "file_type": file_type.value},
        ip_address=ip,
    )
    await db.commit()
    job_id = job.id

    counts = {"products_created": 0, "products_updated": 0, "batches_created": 0, "alerts_generated": 0}
    errors: list[dict[str, Any]] = []
    parsed: ParseResult | None = None
    new_batch_ids: list[uuid.UUID] = []

    async def fail(message: str, details: list[dict[str, Any]]) -> None:
        await db.rollback()
        await db.refresh(job)
        job.status = ImportStatus.FAILED
        job.error_details = details[:MAX_ERRORS_STORED] or [
            {"row": None, "field": None, "value": None, "error_type": "FILE", "message": message}
        ]
        job.failed_rows = parsed.total_rows if parsed else 0
        job.total_rows = parsed.total_rows if parsed else 0
        job.successful_rows = 0
        job.duplicate_rows = parsed.duplicate_rows if parsed else 0
        job.summary = {**counts, "message": message}
        job.completed_at = utcnow()
        audit_service.record(
            db, action=AuditAction.IMPORT_FAILED, company_id=user.company_id, user_id=user.id,
            entity_type="import_job", entity_id=job_id, metadata={"reason": message}, ip_address=ip,
        )
        await db.commit()

    try:
        df = read_excel(content) if file_type == ImportFileType.XLSX else read_csv(content)
        if len(df) > settings.MAX_IMPORT_ROWS:
            raise ImportFileError(f"File has {len(df)} rows; the maximum is {settings.MAX_IMPORT_ROWS}")
        parsed = validate_dataframe(df, dayfirst=settings.IMPORT_DATE_DAYFIRST)
        errors = list(parsed.errors)
        if parsed.total_rows == 0:
            raise ImportFileError("The file contains no data rows")
        if strict and errors:
            raise ImportFileError("Strict mode: the file has invalid rows, nothing was imported", errors)

        company = await get_company(db, user.company_id)
        today = today_in_tz(company.timezone)

        # Pre-load products referenced by the file (one query) for SKU/barcode matching.
        skus = {r.sku for r in parsed.rows if r.sku}
        barcodes = {r.barcode for r in parsed.rows if r.barcode}
        conditions = []
        if skus:
            conditions.append(Product.sku.in_(skus))
        if barcodes:
            conditions.append(Product.barcode.in_(barcodes))
        existing: list[Product] = []
        if conditions:
            existing = list(
                (await db.scalars(select(Product).where(Product.company_id == user.company_id, or_(*conditions)))).all()
            )
        by_sku = {p.sku: p for p in existing if p.sku}
        by_barcode = {p.barcode: p for p in existing if p.barcode}
        categories: dict[str, Any] = {}
        touched_updates: set[uuid.UUID] = set()

        products_for_rows: list[tuple[ParsedRow, Product]] = []
        for row in parsed.rows:
            product = (by_sku.get(row.sku) if row.sku else None) or (by_barcode.get(row.barcode) if row.barcode else None)
            category_id = None
            if row.category:
                key = row.category.lower()
                if key not in categories:
                    categories[key] = await product_service.get_or_create_category(db, user.company_id, row.category)
                category_id = categories[key].id
            if product is None:
                product = Product(
                    company_id=user.company_id,
                    category_id=category_id,
                    name=row.product_name,
                    sku=row.sku,
                    barcode=row.barcode,
                    brand=row.brand,
                    description=row.description,
                    unit=row.unit or "unit",
                    selling_price=row.selling_price or Decimal("0"),
                    cost_price=row.cost_price or Decimal("0"),
                    is_active=True,
                )
                db.add(product)
                await db.flush()
                counts["products_created"] += 1
            else:
                changed = False
                updates = {
                    "name": row.product_name, "brand": row.brand, "unit": row.unit, "description": row.description,
                    "selling_price": row.selling_price, "cost_price": row.cost_price, "category_id": category_id,
                    "sku": row.sku, "barcode": row.barcode,
                }
                for attr, value in updates.items():
                    if value is not None and getattr(product, attr) != value:
                        if attr in ("sku", "barcode"):
                            clash = by_sku.get(value) if attr == "sku" else by_barcode.get(value)
                            if clash is not None and clash.id != product.id:
                                continue
                        setattr(product, attr, value)
                        changed = True
                if not product.is_active:
                    product.is_active = True
                    changed = True
                if changed and product.id not in touched_updates:
                    touched_updates.add(product.id)
                    counts["products_updated"] += 1
            if product.sku:
                by_sku[product.sku] = product
            if product.barcode:
                by_barcode[product.barcode] = product
            products_for_rows.append((row, product))

        # Batches that already exist in the database are duplicates, not errors that abort.
        pairs = {(p.id, r.batch_number) for r, p in products_for_rows}
        existing_pairs: set[tuple[uuid.UUID, str]] = set()
        if pairs:
            existing_pairs = set(
                (await db.execute(
                    select(Batch.product_id, Batch.batch_number).where(
                        tuple_(Batch.product_id, Batch.batch_number).in_(list(pairs))
                    )
                )).all()
            )
        seen_pairs: set[tuple[uuid.UUID, str]] = set()
        for row, product in products_for_rows:
            pair = (product.id, row.batch_number)
            if pair in existing_pairs or pair in seen_pairs:
                parsed.duplicate_rows += 1
                errors.append({
                    "row": row.row_number, "field": "batch_number", "value": row.batch_number,
                    "error_type": "DUPLICATE",
                    "message": "Batch already exists for this product" if pair in existing_pairs
                    else "Same product (matched by sku/barcode) and batch number appear earlier in the file",
                })
                continue
            seen_pairs.add(pair)
            batch, movement = build_batch(
                product, row.batch_number, row.expiry_date, row.quantity, today, row.manufacturing_date
            )
            movement.reference_type = "IMPORT"
            movement.reference_id = job_id
            movement.notes = f"Imported from {filename[:100]} row {row.row_number}"
            db.add(batch)
            db.add(movement)
            new_batch_ids.append(batch.id)
            counts["batches_created"] += 1
        await db.flush()

        failed = len(parsed.failed_row_numbers)
        job.total_rows = parsed.total_rows
        job.successful_rows = counts["batches_created"]
        job.failed_rows = failed
        job.duplicate_rows = parsed.duplicate_rows
        job.error_details = errors[:MAX_ERRORS_STORED]
        job.status = ImportStatus.COMPLETED if not errors else ImportStatus.COMPLETED_WITH_ERRORS
        job.summary = dict(counts)
        job.completed_at = utcnow()
        audit_service.record(
            db, action=AuditAction.IMPORT_COMPLETED, company_id=user.company_id, user_id=user.id,
            entity_type="import_job", entity_id=job_id,
            metadata={**counts, "failed_rows": failed, "duplicate_rows": parsed.duplicate_rows}, ip_address=ip,
        )
        await db.commit()
    except ImportFileError as exc:
        await fail(exc.message, exc.details)
        errors = exc.details or [{"row": None, "field": None, "value": None, "error_type": "FILE", "message": exc.message}]
    except Exception:  # noqa: BLE001 - any DB/logic failure must roll back the whole import
        logger.exception("Import %s failed", job_id)
        await fail("Import failed due to an internal error; no data was written", [])
        errors = [{"row": None, "field": None, "value": None, "error_type": "FILE",
                   "message": "Import failed due to an internal error; no data was written"}]
        counts = {k: 0 for k in counts}

    if new_batch_ids and job.status != ImportStatus.FAILED:
        try:
            check = await run_expiry_check(db, user.company_id, actor_id=user.id, batch_ids=new_batch_ids)
            counts["alerts_generated"] = check.alerts_created
            await db.refresh(job)
            job.summary = dict(counts)
            await db.commit()
        except Exception:  # noqa: BLE001 - data is committed; the daily job will retry alerts
            logger.exception("Post-import expiry check failed for job %s", job_id)
            await db.rollback()

    await db.refresh(job)
    return ImportResult(
        job_id=job.id,
        status=job.status,
        total_rows=job.total_rows,
        successful_rows=job.successful_rows,
        failed_rows=job.failed_rows,
        duplicate_rows=job.duplicate_rows,
        products_created=counts["products_created"] if job.status != ImportStatus.FAILED else 0,
        products_updated=counts["products_updated"] if job.status != ImportStatus.FAILED else 0,
        batches_created=counts["batches_created"] if job.status != ImportStatus.FAILED else 0,
        alerts_generated=counts["alerts_generated"],
        errors=[ImportRowError(**e) for e in errors[:100]],
    )
