import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.core.exceptions import error_responses
from app.dependencies.auth import ManagerUser, StaffUser
from app.dependencies.database import DbSession
from app.schemas import Page
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.services import product_service
from app.utils.helpers import client_ip
from app.utils.pagination import PageParams, page_meta, page_params

router = APIRouter(prefix="/categories", tags=["Categories"])


@router.get("", response_model=Page[CategoryRead], summary="List categories",
            description="Company categories with active product counts.", responses=error_responses(401))
async def list_categories(
    user: StaffUser, db: DbSession, params: PageParams = Depends(page_params),
    search: str | None = Query(None, max_length=100),
) -> Page[CategoryRead]:
    items, total = await product_service.list_categories(db, user.company_id, params, search)
    return Page(items=items, **page_meta(total, params))


@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED, summary="Create category",
             description="Names are unique per company (case-insensitive). MANAGER or ADMIN.",
             responses=error_responses(401, 403, 409, 422))
async def create_category(data: CategoryCreate, user: ManagerUser, request: Request, db: DbSession) -> CategoryRead:
    return await product_service.create_category(db, user, data, client_ip(request))


@router.get("/{category_id}", response_model=CategoryRead, summary="Get category",
            description="Single category.", responses=error_responses(401, 404))
async def get_category(category_id: uuid.UUID, user: StaffUser, db: DbSession) -> CategoryRead:
    return await product_service.get_category_read(db, user.company_id, category_id)


@router.patch("/{category_id}", response_model=CategoryRead, summary="Update category",
              description="MANAGER or ADMIN.", responses=error_responses(401, 403, 404, 409, 422))
async def update_category(
    category_id: uuid.UUID, data: CategoryUpdate, user: ManagerUser, request: Request, db: DbSession
) -> CategoryRead:
    return await product_service.update_category(db, user, category_id, data, client_ip(request))


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete category",
               description="Products in the category become uncategorised. MANAGER or ADMIN.",
               responses=error_responses(401, 403, 404))
async def delete_category(category_id: uuid.UUID, user: ManagerUser, request: Request, db: DbSession) -> Response:
    await product_service.delete_category(db, user, category_id, client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
