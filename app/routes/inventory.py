import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request

from app.core.exceptions import error_responses
from app.dependencies.auth import ManagerUser, StaffUser
from app.dependencies.database import DbSession
from app.models import MovementType
from app.schemas import Page
from app.schemas.inventory import (
    InventoryAdjustRequest,
    InventoryAdjustResponse,
    InventoryItem,
    InventorySummary,
    MovementRead,
)
from app.services import inventory_service
from app.utils.helpers import client_ip
from app.utils.pagination import PageParams, page_meta, page_params

router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get("", response_model=Page[InventoryItem], summary="Stock by product",
            description="Per-product totals: all units, sellable (non-expired), expired, batch count, nearest expiry, "
                        "cost value and low-stock flag.",
            responses=error_responses(401, 422))
async def list_inventory(
    user: StaffUser, db: DbSession, params: PageParams = Depends(page_params),
    search: str | None = Query(None, max_length=100), category_id: uuid.UUID | None = None,
    low_stock_only: bool = False,
) -> Page[InventoryItem]:
    items, total = await inventory_service.list_inventory(
        db, user.company_id, params, search=search, category_id=category_id, low_stock_only=low_stock_only
    )
    return Page(items=items, **page_meta(total, params))


@router.get("/summary", response_model=InventorySummary, summary="Inventory summary",
            description="Company-wide units by expiry status and inventory value at cost and retail.",
            responses=error_responses(401))
async def inventory_summary(user: StaffUser, db: DbSession) -> InventorySummary:
    return await inventory_service.summary(db, user.company_id)


@router.post("/adjust", response_model=InventoryAdjustResponse, summary="Adjust stock",
             description="Signed change to one batch (positive adds, negative removes). DAMAGED/EXPIRED/TRANSFER must "
                         "be negative; PURCHASE/RETURN positive. Negative stock is rejected unless "
                         "ALLOW_NEGATIVE_INVENTORY=true. MANAGER or ADMIN.",
             responses=error_responses(401, 403, 404, 409, 422))
async def adjust_inventory(
    data: InventoryAdjustRequest, user: ManagerUser, request: Request, db: DbSession
) -> InventoryAdjustResponse:
    return await inventory_service.adjust(db, user, data, client_ip(request))


@router.get("/movements", response_model=Page[MovementRead], summary="Movement ledger",
            description="Every stock change, newest first. Quantities are signed.",
            responses=error_responses(401, 422))
async def list_movements(
    user: StaffUser, db: DbSession, params: PageParams = Depends(page_params),
    product_id: uuid.UUID | None = None, batch_id: uuid.UUID | None = None,
    movement_type: MovementType | None = None, date_from: date | None = None, date_to: date | None = None,
) -> Page[MovementRead]:
    items, total = await inventory_service.list_movements(
        db, user.company_id, params, product_id=product_id, batch_id=batch_id, movement_type=movement_type,
        date_from=date_from, date_to=date_to,
    )
    return Page(items=[MovementRead.model_validate(m) for m in items], **page_meta(total, params))
