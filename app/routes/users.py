import uuid

from fastapi import APIRouter, Depends, Request, status

from app.core.exceptions import error_responses
from app.dependencies.auth import AdminUser
from app.dependencies.database import DbSession
from app.schemas import Page
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.services import auth_service
from app.utils.helpers import client_ip
from app.utils.pagination import PageParams, page_meta, page_params

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("", response_model=Page[UserRead], summary="List users",
            description="Users in your company. ADMIN only.", responses=error_responses(401, 403))
async def list_users(user: AdminUser, db: DbSession, params: PageParams = Depends(page_params)) -> Page[UserRead]:
    items, total = await auth_service.list_users(db, user.company_id, params)
    return Page(items=[UserRead.model_validate(u) for u in items], **page_meta(total, params))


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED, summary="Create user",
             description="Add a MANAGER, STAFF or ADMIN user to your company. ADMIN only.",
             responses=error_responses(401, 403, 409, 422))
async def create_user(data: UserCreate, user: AdminUser, request: Request, db: DbSession) -> UserRead:
    return UserRead.model_validate(await auth_service.create_user(db, user, data, client_ip(request)))


@router.get("/{user_id}", response_model=UserRead, summary="Get user",
            description="ADMIN only.", responses=error_responses(401, 403, 404))
async def get_user(user_id: uuid.UUID, user: AdminUser, db: DbSession) -> UserRead:
    return UserRead.model_validate(await auth_service.get_user(db, user.company_id, user_id))


@router.patch("/{user_id}", response_model=UserRead, summary="Update user",
              description="Change name, phone, role or active flag. A company always keeps one active admin.",
              responses=error_responses(400, 401, 403, 404, 422))
async def update_user(user_id: uuid.UUID, data: UserUpdate, user: AdminUser, request: Request, db: DbSession) -> UserRead:
    return UserRead.model_validate(await auth_service.update_user(db, user, user_id, data, client_ip(request)))
