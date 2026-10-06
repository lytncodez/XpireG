import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.exceptions import error_responses
from app.dependencies.auth import StaffUser
from app.dependencies.database import DbSession
from app.schemas import Page
from app.schemas.sales import SaleCreate, SaleCreateResult, SaleRead, SalesSummary, SalesTrends
from app.services import sales_service
from app.utils.helpers import client_ip
from app.utils.pagination import PageParams, page_meta, page_params

router = APIRouter(prefix="/sales", tags=["Sales"])


@router.get("", response_model=Page[SaleRead], summary="List sales",
            description="Newest first; filter by product and local date range.", responses=error_responses(401, 422))
async def list_sales(
    user: StaffUser, db: DbSession, params: PageParams = Depends(page_params),
    product_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
) -> Page[SaleRead]:
    items, total = await sales_service.list_sales(
        db, user.company_id, params, product_id=product_id, date_from=date_from, date_to=date_to
    )
    return Page(items=items, **page_meta(total, params))


@router.post("", response_model=SaleCreateResult, status_code=status.HTTP_201_CREATED, summary="Record a sale",
             description="Validates product, batch and quantity, decrements stock, creates the sale and SALE movement "
                         "in one transaction. Without batch_id, stock is allocated First-Expired-First-Out across "
                         "non-expired batches (one sale record per batch used). Expired batches cannot be sold.",
             responses=error_responses(400, 401, 404, 409, 422))
async def create_sale(data: SaleCreate, user: StaffUser, request: Request, db: DbSession) -> SaleCreateResult:
    return await sales_service.record_sale(db, user, data, client_ip(request))


@router.get("/summary", response_model=SalesSummary, summary="Sales summary",
            description="Revenue, units, transactions, average sale and estimated gross profit for a date range "
                        "(default: last 30 days).",
            responses=error_responses(400, 401, 422))
async def sales_summary(
    user: StaffUser, db: DbSession, date_from: date | None = None, date_to: date | None = None
) -> SalesSummary:
    return await sales_service.summary(db, user.company_id, date_from, date_to)


@router.get("/trends", response_model=SalesTrends, summary="Sales trends",
            description="Zero-filled time series by day, week or month.", responses=error_responses(400, 401, 422))
async def sales_trends(
    user: StaffUser, db: DbSession,
    granularity: str = Query("day", pattern="^(day|week|month)$"),
    periods: int = Query(30, ge=1, le=366),
) -> SalesTrends:
    return await sales_service.trends(db, user.company_id, granularity, periods)


@router.get("/{sale_id}", response_model=SaleRead, summary="Get sale",
            description="Single sale record.", responses=error_responses(401, 404))
async def get_sale(sale_id: uuid.UUID, user: StaffUser, db: DbSession) -> SaleRead:
    return await sales_service.get_sale(db, user.company_id, sale_id)
