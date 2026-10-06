"""Seed a realistic demo company for ExpireGuard.

    python scripts/seed_demo_data.py              # (re)create the demo company
    python scripts/seed_demo_data.py --run-expiry # ...and run the expiry check (alerts + mock SMS)

Creates categories, ~17 products, current batches across every expiry band (including one
expiring TOMORROW and some already expired), 90 days of sales history with fast, slow,
spiking, declining and low-stock/high-sales profiles, and the matching inventory movements.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models import (  # noqa: E402
    Batch,
    Category,
    Company,
    InventoryMovement,
    MovementType,
    Product,
    Sale,
    User,
    UserRole,
)
from app.services.expiry_service import batch_status, run_expiry_check  # noqa: E402
from app.utils.dates import today_in_tz  # noqa: E402

COMPANY_EMAIL = "office@demo.expireguard.local"
ADMIN_EMAIL = "admin@demo.expireguard.local"
MANAGER_EMAIL = "manager@demo.expireguard.local"
STAFF_EMAIL = "staff@demo.expireguard.local"
PASSWORD = "DemoPass123"
TIMEZONE = "Africa/Nairobi"
HISTORY_DAYS = 90


@dataclass
class Spec:
    name: str
    sku: str
    barcode: str
    category: str
    unit: str
    price: str
    cost: str
    profile: str
    batches: list[tuple[int, int]]  # (days until expiry, remaining quantity)


PRODUCTS: list[Spec] = [
    Spec("Fresh Milk 1L", "DAI-MLK-1L", "6001000000011", "Dairy", "bottle", "1.50", "1.05", "fast",
         [(1, 40), (12, 120), (60, 200)]),  # batch expiring TOMORROW
    Spec("Natural Yoghurt 500g", "DAI-YOG-500", "6001000000028", "Dairy", "tub", "2.40", "1.60", "medium",
         [(-3, 15), (20, 60)]),
    Spec("Cheddar Cheese 200g", "DAI-CHD-200", "6001000000035", "Dairy", "pack", "4.80", "3.20", "slow",
         [(150, 400)]),
    Spec("Salted Butter 250g", "DAI-BUT-250", "6001000000042", "Dairy", "block", "3.10", "2.20", "spike",
         [(45, 80), (120, 100)]),
    Spec("White Bread Loaf", "BAK-BRD-WHT", "6001000000059", "Bakery", "loaf", "1.20", "0.70", "fast",
         [(2, 30), (5, 80)]),
    Spec("Butter Croissants 4pk", "BAK-CRS-4PK", "6001000000066", "Bakery", "pack", "3.50", "2.10", "decline",
         [(3, 50)]),
    Spec("Orange Juice 1L", "BEV-OJ-1L", "6001000000073", "Beverages", "carton", "2.20", "1.40", "medium",
         [(25, 90), (200, 150)]),
    Spec("Mineral Water 500ml", "BEV-WTR-500", "6001000000080", "Beverages", "bottle", "0.60", "0.25", "fast",
         [(400, 600)]),
    Spec("Energy Drink 250ml", "BEV-NRG-250", "6001000000097", "Beverages", "can", "1.80", "1.10", "slow",
         [(300, 500)]),
    Spec("Basmati Rice 2kg", "PAN-RCE-2KG", "6001000000103", "Pantry", "bag", "5.50", "3.90", "medium",
         [(500, 200)]),
    Spec("Canned Beans 400g", "PAN-BNS-400", "6001000000110", "Pantry", "can", "1.10", "0.65", "slow",
         [(-10, 20), (700, 300)]),
    Spec("Spaghetti 500g", "PAN-SPG-500", "6001000000127", "Pantry", "pack", "1.30", "0.80", "medium",
         [(365, 150)]),
    Spec("Paracetamol 500mg x20", "PHA-PCM-500", "6001000000134", "Pharmacy", "box", "2.00", "0.90", "medium",
         [(28, 40), (80, 60)]),
    Spec("Vitamin C 1000mg x30", "PHA-VTC-1000", "6001000000141", "Pharmacy", "bottle", "6.50", "3.80", "slow",
         [(85, 300)]),
    Spec("Infant Formula 400g", "BAB-FRM-400", "6001000000158", "Baby", "tin", "12.00", "8.50", "tight",
         [(40, 8)]),  # low stock / high sales
    Spec("Potato Crisps 150g", "SNK-CRS-150", "6001000000165", "Snacks", "bag", "1.60", "0.90", "medium",
         [(15, 70)]),
    Spec("Milk Chocolate Bar", "SNK-CHO-100", "6001000000172", "Snacks", "bar", "1.25", "0.70", "fast",
         [(35, 100), (90, 250)]),
]


def daily_units(profile: str, days_ago: int, rng: random.Random) -> int:
    recent = days_ago < 7
    if profile == "fast":
        return rng.randint(18, 30)
    if profile == "medium":
        return rng.randint(4, 10)
    if profile == "slow":
        return 1 if rng.random() < 0.12 else 0
    if profile == "spike":
        return rng.randint(16, 22) if recent else rng.randint(4, 6)
    if profile == "decline":
        return rng.randint(0, 1) if recent else rng.randint(8, 12)
    if profile == "tight":
        return rng.randint(9, 12)  # outsells the median product while holding ~1 day of stock
    return 0


async def seed(
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
    *,
    reset: bool = True,
    run_expiry: bool = False,
) -> dict:
    rng = random.Random(42)
    async with session_factory() as db:
        existing = await db.scalar(select(Company.id).where(Company.email == COMPANY_EMAIL))
        if existing:
            if not reset:
                raise SystemExit("Demo company already exists (use --reset to recreate it)")
            await db.execute(delete(Company).where(Company.id == existing))
            await db.commit()

        company = Company(
            name="Demo Fresh Mart", email=COMPANY_EMAIL, phone="+254700123000",
            address="Moi Avenue, Nairobi", currency="KES", timezone=TIMEZONE,
        )
        db.add(company)
        await db.flush()
        pw = hash_password(PASSWORD)
        db.add_all([
            User(company_id=company.id, name="Amina Admin", email=ADMIN_EMAIL, phone_number="+254700123001",
                 password_hash=pw, role=UserRole.ADMIN),
            User(company_id=company.id, name="Brian Manager", email=MANAGER_EMAIL, phone_number="+254700123002",
                 password_hash=pw, role=UserRole.MANAGER),
            User(company_id=company.id, name="Chao Staff", email=STAFF_EMAIL, phone_number=None,
                 password_hash=pw, role=UserRole.STAFF),
        ])

        today = today_in_tz(TIMEZONE)
        categories: dict[str, Category] = {}
        stats = {"products": 0, "batches": 0, "sales": 0, "movements": 0, "units_sold": 0}

        for spec in PRODUCTS:
            if spec.category not in categories:
                categories[spec.category] = Category(company_id=company.id, name=spec.category)
                db.add(categories[spec.category])
                await db.flush()
            product = Product(
                company_id=company.id, category_id=categories[spec.category].id, name=spec.name, sku=spec.sku,
                barcode=spec.barcode, unit=spec.unit, selling_price=Decimal(spec.price), cost_price=Decimal(spec.cost),
                brand="Demo", is_active=True,
            )
            db.add(product)
            await db.flush()
            stats["products"] += 1

            # 1. Sales history drawn from an older, now depleted batch.
            history = [(d, daily_units(spec.profile, d, rng)) for d in range(HISTORY_DAYS, 0, -1)]
            history.append((0, daily_units(spec.profile, 0, rng) // 2))  # part of today
            total_hist = sum(u for _, u in history)
            received_at = datetime.combine(today - timedelta(days=HISTORY_DAYS + 5), time(8), tzinfo=timezone.utc)
            if total_hist:
                hist = Batch(
                    id=uuid.uuid4(), company_id=company.id, product_id=product.id, batch_number=f"HIST-{spec.sku}",
                    manufacturing_date=today - timedelta(days=HISTORY_DAYS + 10),
                    expiry_date=today + timedelta(days=3650 if spec.profile != "fast" else 30),
                    initial_quantity=total_hist, remaining_quantity=0, status=batch_status(today, 0, today),
                    created_at=received_at, updated_at=received_at,
                )
                db.add(hist)
                await db.flush()
                db.add(InventoryMovement(
                    company_id=company.id, product_id=product.id, batch_id=hist.id,
                    movement_type=MovementType.PURCHASE, quantity=total_hist, reference_type="BATCH",
                    reference_id=hist.id, notes="Seed: historical stock", created_at=received_at,
                ))
                stats["batches"] += 1
                stats["movements"] += 1
                for days_ago, units in history:
                    if units <= 0:
                        continue
                    sold_at = datetime.combine(today - timedelta(days=days_ago), time(9), tzinfo=timezone.utc)
                    sold_at += timedelta(minutes=rng.randint(0, 600))
                    if days_ago == 0:
                        sold_at = min(sold_at, datetime.now(timezone.utc) - timedelta(minutes=1))
                    sale = Sale(
                        id=uuid.uuid4(), company_id=company.id, product_id=product.id, batch_id=hist.id,
                        quantity=units, unit_price=product.selling_price,
                        total_amount=product.selling_price * units, sold_at=sold_at, created_at=sold_at,
                    )
                    db.add(sale)
                    db.add(InventoryMovement(
                        company_id=company.id, product_id=product.id, batch_id=hist.id,
                        movement_type=MovementType.SALE, quantity=-units, reference_type="SALE",
                        reference_id=sale.id, created_at=sold_at,
                    ))
                    stats["sales"] += 1
                    stats["movements"] += 1
                    stats["units_sold"] += units

            # 2. Current batches across the expiry bands.
            for i, (days, qty) in enumerate(spec.batches, start=1):
                expiry = today + timedelta(days=days)
                mfg = min(expiry, today) - timedelta(days=30)
                created = datetime.combine(today - timedelta(days=rng.randint(3, 20)), time(8), tzinfo=timezone.utc)
                batch = Batch(
                    id=uuid.uuid4(), company_id=company.id, product_id=product.id,
                    batch_number=f"{spec.sku.split('-')[1]}-{today:%y%m}-{i:02d}",
                    manufacturing_date=mfg, expiry_date=expiry, initial_quantity=qty, remaining_quantity=qty,
                    status=batch_status(expiry, qty, today), created_at=created, updated_at=created,
                )
                db.add(batch)
                await db.flush()
                db.add(InventoryMovement(
                    company_id=company.id, product_id=product.id, batch_id=batch.id,
                    movement_type=MovementType.PURCHASE, quantity=qty, reference_type="BATCH",
                    reference_id=batch.id, notes="Seed: current stock", created_at=created,
                ))
                stats["batches"] += 1
                stats["movements"] += 1

        await db.commit()
        result = {"company_id": str(company.id), "as_of": today.isoformat(), **stats}
        if run_expiry:
            check = await run_expiry_check(db, company.id)
            result["expiry_check"] = {k: v for k, v in check.as_dict().items() if k != "new_alert_ids"}
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-reset", action="store_true", help="Fail instead of recreating an existing demo company")
    parser.add_argument("--run-expiry", action="store_true", help="Run the expiry check after seeding")
    args = parser.parse_args()
    result = asyncio.run(seed(reset=not args.no_reset, run_expiry=args.run_expiry))
    print("Demo data seeded:")
    for key, value in result.items():
        print(f"  {key}: {value}")
    print("\nLogin credentials (password for all: %s)" % PASSWORD)
    print(f"  ADMIN   {ADMIN_EMAIL}\n  MANAGER {MANAGER_EMAIL}\n  STAFF   {STAFF_EMAIL}")


if __name__ == "__main__":
    main()
