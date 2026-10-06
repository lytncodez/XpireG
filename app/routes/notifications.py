import uuid

from fastapi import APIRouter, Depends

from app.core.exceptions import error_responses
from app.dependencies.auth import ManagerUser, StaffUser
from app.dependencies.database import DbSession
from app.models import NotificationStatus
from app.schemas import Page
from app.schemas.notifications import NotificationRead
from app.services import notification_service
from app.utils.pagination import PageParams, page_meta, page_params

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=Page[NotificationRead], summary="List notifications",
            description="SMS delivery records (one per recipient) with provider ids and errors.",
            responses=error_responses(401, 422))
async def list_notifications(
    user: StaffUser, db: DbSession, params: PageParams = Depends(page_params),
    status: NotificationStatus | None = None, alert_id: uuid.UUID | None = None,
) -> Page[NotificationRead]:
    items, total = await notification_service.list_notifications(db, user.company_id, params, status, alert_id)
    return Page(items=[NotificationRead.model_validate(n) for n in items], **page_meta(total, params))


@router.get("/{notification_id}", response_model=NotificationRead, summary="Get notification",
            description="Single delivery record.", responses=error_responses(401, 404))
async def get_notification(notification_id: uuid.UUID, user: StaffUser, db: DbSession) -> NotificationRead:
    return NotificationRead.model_validate(
        await notification_service.get_notification(db, user.company_id, notification_id)
    )


@router.post("/{notification_id}/retry", response_model=NotificationRead, summary="Retry a failed notification",
             description="Re-sends a FAILED SMS. MANAGER or ADMIN.", responses=error_responses(400, 401, 403, 404))
async def retry_notification(notification_id: uuid.UUID, user: ManagerUser, db: DbSession) -> NotificationRead:
    return NotificationRead.model_validate(
        await notification_service.retry_one(db, user.company_id, notification_id)
    )
