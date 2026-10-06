from fastapi import APIRouter, Path

from app.core.exceptions import error_responses
from app.dependencies.auth import StaffUser
from app.dependencies.database import DbSession
from app.schemas.barcode import BarcodeLookupRequest, BarcodeLookupResponse
from app.services import barcode_service

router = APIRouter(prefix="/barcode", tags=["Barcode"])

DESCRIPTION = (
    "The scanner (frontend/admin app) captures the barcode string and sends it here. A barcode identifies the "
    "**product only**; batch numbers and expiry dates are separate records. Returns the product, its in-stock "
    "batches in FEFO order with live expiry status, and inventory totals. Unknown barcodes return 404 NOT_FOUND."
)


@router.get("/lookup/{barcode}", response_model=BarcodeLookupResponse, summary="Look up a barcode (GET)",
            description=DESCRIPTION, responses=error_responses(400, 401, 404))
async def lookup_get(
    user: StaffUser, db: DbSession, barcode: str = Path(..., min_length=1, max_length=64)
) -> BarcodeLookupResponse:
    return await barcode_service.lookup(db, user.company_id, barcode)


@router.post("/lookup", response_model=BarcodeLookupResponse, summary="Look up a barcode (POST)",
             description=DESCRIPTION, responses=error_responses(400, 401, 404, 422))
async def lookup_post(data: BarcodeLookupRequest, user: StaffUser, db: DbSession) -> BarcodeLookupResponse:
    return await barcode_service.lookup(db, user.company_id, data.barcode)
