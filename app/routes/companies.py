from fastapi import APIRouter, Depends, Query, Request

from app.core.exceptions import error_responses
from app.dependencies.auth import AdminUser, StaffUser
from app.dependencies.database import DbSession
from app.schemas import Page
from app.schemas.company import AuditLogRead, CompanyRead, CompanyUpdate
from app.services import audit_service, auth_service
from app.utils.helpers import client_ip
from app.utils.pagination import PageParams, page_meta, page_params

router = APIRouter(prefix="/companies", tags=["Company"])


@router.get("/me", response_model=CompanyRead, summary="My company",
            description="The company the current user belongs to.", responses=error_responses(401))
async def get_my_company(user: StaffUser, db: DbSession) -> CompanyRead:
    return CompanyRead.model_validate(await auth_service.get_company(db, user.company_id))


@router.patch("/me", response_model=CompanyRead, summary="Update my company",
              description="Update name, phone, address, currency or timezone. ADMIN only. "
                          "The timezone defines 'today' for expiry calculations.",
              responses=error_responses(401, 403, 422))
async def update_my_company(data: CompanyUpdate, user: AdminUser, request: Request, db: DbSession) -> CompanyRead:
    return CompanyRead.model_validate(await auth_service.update_company(db, user, data, client_ip(request)))


@router.get("/me/audit-logs", response_model=Page[AuditLogRead], summary="Audit trail",
            description="Company audit log, newest first. ADMIN only.", responses=error_responses(401, 403))
async def audit_logs(
    user: AdminUser,
    db: DbSession,
    params: PageParams = Depends(page_params),
    action: str | None = Query(None, description="e.g. USER_LOGIN, SALE_CREATED"),
    entity_type: str | None = Query(None),
) -> Page[AuditLogRead]:
    items, total = await audit_service.list_logs(db, user.company_id, params, action, entity_type)
    return Page(items=[AuditLogRead.model_validate(i) for i in items], **page_meta(total, params))
