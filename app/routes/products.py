import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.core.exceptions import error_responses
from app.dependencies.auth import AdminUser, ManagerUser, StaffUser
from app.dependencies.database import DbSession
from app.schemas import Page
from app.schemas.product import ProductCreate, ProductRead, ProductUpdate
from app.services import product_service
from app.utils.helpers import client_ip
from app.utils.pagination import PageParams, page_meta, page_params

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("", response_model=Page[ProductRead], summary="List / search products",
            description="Search by name, SKU, barcode or brand; filter by category; exact barcode or SKU lookup. "
                        "Each item includes total and sellable stock.",
            responses=error_responses(401, 422))
async def list_products(
    user: StaffUser,
    db: DbSession,
    params: PageParams = Depends(page_params),
    search: str | None = Query(None, max_length=100),
    category_id: uuid.UUID | None = None,
    barcode: str | None = Query(None, max_length=64),
    sku: str | None = Query(None, max_length=64),
    include_inactive: bool = False,
) -> Page[ProductRead]:
    items, total = await product_service.list_products(
        db, user.company_id, params, search=search, category_id=category_id, barcode=barcode, sku=sku,
        include_inactive=include_inactive,
    )
    return Page(items=items, **page_meta(total, params))


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED, summary="Create product",
             description="SKU and barcode are unique within your company. At least one is required. MANAGER or ADMIN.",
             responses=error_responses(401, 403, 404, 409, 422))
async def create_product(data: ProductCreate, user: ManagerUser, request: Request, db: DbSession) -> ProductRead:
    return await product_service.create_product(db, user, data, client_ip(request))


@router.get("/{product_id}", response_model=ProductRead, summary="Get product",
            description="Product with live stock figures.", responses=error_responses(401, 404))
async def get_product(product_id: uuid.UUID, user: StaffUser, db: DbSession) -> ProductRead:
    return await product_service.get_product_read(db, user.company_id, product_id)


@router.patch("/{product_id}", response_model=ProductRead, summary="Update product",
              description="Partial update. Set is_active=true to reactivate a deleted product. MANAGER or ADMIN.",
              responses=error_responses(401, 403, 404, 409, 422))
async def update_product(
    product_id: uuid.UUID, data: ProductUpdate, user: ManagerUser, request: Request, db: DbSession
) -> ProductRead:
    return await product_service.update_product(db, user, product_id, data, client_ip(request))


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete product",
               description="Soft delete (is_active=false); sales and inventory history are preserved. ADMIN only.",
               responses=error_responses(401, 403, 404))
async def delete_product(product_id: uuid.UUID, user: AdminUser, request: Request, db: DbSession) -> Response:
    await product_service.delete_product(db, user, product_id, client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
