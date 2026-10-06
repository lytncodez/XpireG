# ExpireGuard — Backend (Phase 1 + Phase 2 AI layer)

ExpireGuard is an inventory **expiry monitoring and business intelligence** backend. It tracks products and their
batches, records sales First-Expired-First-Out, classifies every batch by days to expiry, raises de-duplicated alerts,
sends SMS notifications, and serves deterministic analytics, anomaly detection and dashboard data to a frontend.

Phase 1 is the deterministic operational backend: every number is computed with SQL and Python.
Phase 2 adds an **AI intelligence layer on top** (see [AI intelligence layer](#ai-intelligence-layer-phase-2)).
The AI explains verified data; it never calculates business numbers and never touches the database.

```
Business → Products → Batches → Inventory → Sales → Expiry Engine → Alerts → SMS → Analytics → Dashboard
```

---

## Contents
1. [Architecture](#architecture) · 2. [Stack](#stack) · 3. [Project structure](#project-structure) ·
4. [Environment variables](#environment-variables) · 5. [Quick start with Docker](#quick-start-with-docker) ·
6. [Local setup](#local-setup-without-docker) · 7. [Database & migrations](#database--migrations) ·
8. [API documentation](#api-documentation) · 9. [Authentication & roles](#authentication--roles) ·
10. [Products, batches, barcode](#products-batches-and-barcodes) · 11. [CSV / Excel import](#csv--excel-import) ·
12. [Inventory & sales](#inventory--sales) · 13. [Expiry engine](#expiry-engine) · 14. [Alerts](#alerts) ·
15. [SMS](#sms) · 16. [Analytics & anomalies](#analytics--anomaly-detection) · 17. [Dashboard](#dashboard) ·
18. [Background jobs](#background-jobs) · 19. [Security](#security) · 20. [Testing](#testing) ·
21. [Demo workflow](#demo-workflow) · 22. [Design notes](#design-notes) · 23. [AI intelligence layer](#ai-intelligence-layer-phase-2)

---

## Architecture

```
HTTP ──► routes/        thin: validate input (Pydantic), check role, call a service, shape the response
          │
          ▼
        services/       all business logic and transactions
          │
          ▼
        models/ ──► PostgreSQL (SQLAlchemy 2 async + asyncpg)

jobs/   APScheduler (in-process) calls the same services: expiry, alert maintenance, cleanup
```

Alert pipeline:

```
Expiry Engine ─► Alert Service ─► Notification Service ─► SMS Service ─► SMS Provider (mock | Africa's Talking | Twilio)
 (statuses)       (dedupe)         (one record per          (never raises;
                                    recipient, retries)      failures become data)
```

* **Tenant isolation:** every business table carries `company_id`; every query filters by the caller's company.
  Requests for another company's records return **404**, so existence is never leaked.
* **Transactions:** sales, adjustments, imports, batch operations and alert creation each run in a single
  transaction, with row locks (`SELECT … FOR UPDATE`) on batches whose stock changes.
* **SMS is sent after the alert transaction commits**, so a provider outage can never roll back or crash expiry
  processing.

## Stack

Python 3.12 · FastAPI · Uvicorn · PostgreSQL 16 · SQLAlchemy 2 (async, asyncpg) · Alembic · Pydantic v2 ·
pydantic-settings · Pandas · openpyxl · python-multipart · httpx · PyJWT · argon2-cffi · APScheduler 3 ·
pytest · pytest-asyncio

## Project structure

```
backend/
├── app/
│   ├── main.py                 app factory, CORS, request-id/security-header middleware, lifespan (scheduler)
│   ├── core/                   config, database, security (Argon2 + JWT), logging, exceptions
│   ├── models/                 ORM models (12 tables)
│   ├── schemas/                Pydantic request/response contracts (+ reusable field types in types.py)
│   ├── routes/                 one router per resource
│   ├── services/               business logic (see list below)
│   ├── jobs/                   expiry_job, alert_job, cleanup_job (+ scheduler in __init__)
│   ├── utils/                  dates, validators, pagination, helpers (money, client IP, rate limiter)
│   └── dependencies/           DB session, auth/RBAC, rate limiting
├── alembic/                    async env + versions/0001_initial_schema.py
├── scripts/
│   ├── seed_demo_data.py       realistic demo company
│   ├── make_sample_imports.py  generates sample CSV/XLSX with dates relative to today
│   └── init-test-db.sql        creates the test database in Docker
├── tests/                      pytest suite (PostgreSQL)
├── Dockerfile · docker-compose.yml · requirements.txt · alembic.ini · pytest.ini · .env.example
```

Services: `auth_service` (auth, users, company), `audit_service`, `product_service` (products + categories),
`batch_service`, `inventory_service`, `sales_service`, `expiry_service`, `alert_service`, `notification_service`,
`sms_service`, `csv_service` (shared import pipeline + CSV reader), `excel_service` (XLSX reader),
`barcode_service`, `analytics_service`, `anomaly_service`, `dashboard_service`.

## Environment variables

Copy `.env.example` to `.env`. `.env` is git-ignored and must never be committed.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `development` / `test` / `production`. Production enforces a strong JWT secret and hides reset tokens. |
| `DATABASE_URL` | local PostgreSQL | `postgresql+asyncpg://user:pass@host:5432/db` |
| `TEST_DATABASE_URL` | `…/expireguard_test` | Database used by pytest (wiped on every run). |
| `JWT_SECRET_KEY` | **required** | ≥32 random chars in production. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token lifetime. |
| `RESET_TOKEN_EXPIRE_MINUTES` | `30` | Password reset token lifetime. |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Comma-separated frontend origins. |
| `EXPIRY_CRITICAL_DAYS` / `EXPIRY_SOON_DAYS` | `30` / `90` | Expiry thresholds. |
| `ALLOW_NEGATIVE_INVENTORY` | `false` | Allow stock below zero. |
| `LOW_STOCK_THRESHOLD` | `10` | Sellable units at/below which LOW_STOCK alerts fire. |
| `SMS_MODE` | `mock` | `mock` or `live`. |
| `SMS_PROVIDER` | `africastalking` | `africastalking` or `twilio` (live mode). |
| `SMS_MIN_SEVERITY` | `CRITICAL` | Lowest alert severity that triggers SMS. |
| `AT_USERNAME`, `AT_API_KEY`, `AT_SANDBOX`, `SMS_SENDER_ID` | – | Africa's Talking credentials. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | – | Twilio credentials. |
| `MAX_UPLOAD_SIZE_MB` / `MAX_IMPORT_ROWS` | `10` / `50000` | Import limits. |
| `IMPORT_DATE_DAYFIRST` | `true` | `03/04/2027` = 3 April. |
| `RATE_LIMIT_*` | enabled, 10/min auth, 5/min uploads | Per-IP limits. |
| `ANOMALY_*` | see `.env.example` | Anomaly detection windows and thresholds. |
| `SCHEDULER_ENABLED`, `EXPIRY_JOB_HOUR`, … | daily 06:00 UTC | Background job schedule. |

Live SMS mode refuses to start without the provider's credentials.

## Quick start with Docker

```bash
cp .env.example .env
# edit JWT_SECRET_KEY:  python -c "import secrets; print(secrets.token_urlsafe(48))"
docker compose up --build -d
curl http://localhost:8000/health        # {"status":"ok","database":"ready",...}
open http://localhost:8000/docs
```

The backend container runs `alembic upgrade head` before starting Uvicorn. Compose also creates an
`expireguard_test` database (first start of the volume only) for the test suite:

```bash
docker compose exec backend pytest                                  # run tests inside the container
docker compose exec backend python scripts/seed_demo_data.py --run-expiry
```

> If the `pgdata` volume existed before `init-test-db.sql` was added, create the test DB once:
> `docker compose exec db createdb -U expireguard expireguard_test`

## Local setup (without Docker)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                     # set JWT_SECRET_KEY and DATABASE_URL
createdb expireguard && createdb expireguard_test     # or: docker compose up -d db
alembic upgrade head
uvicorn app.main:app --reload
```

## Database & migrations

Tables: `companies`, `users`, `revoked_tokens`, `categories`, `products`, `batches`, `inventory_movements`, `sales`,
`alerts`, `notifications`, `import_jobs`, `audit_logs`.

Key constraints:
* `products`: unique `(company_id, sku)` and `(company_id, barcode)`; indexes on `sku`, `barcode`; prices ≥ 0.
* `batches`: unique `(product_id, batch_number)`; `expiry_date >= manufacturing_date`; indexes on expiry/status.
* `alerts`: partial unique indexes guarantee **at most one unresolved alert per (batch, type)** and per
  (product, type) for product-level alerts — duplicates are impossible even under concurrent checks.
* `sales`: `quantity > 0`; batch FK is `RESTRICT` (a batch with sales cannot be deleted).
* Foreign keys cascade from `companies`, so deleting a company removes all its data.

```bash
alembic upgrade head                         # apply
alembic downgrade -1                         # roll back
alembic revision --autogenerate -m "msg"     # after changing models
alembic check                                # verify models and migrations agree
```

## API documentation

* Swagger UI: **http://localhost:8000/docs** · ReDoc: **http://localhost:8000/redoc** · OpenAPI: `/openapi.json`
* Every endpoint documents summary, description, request/response schema and error responses.
* Lists are paginated: `?page=1&page_size=20` → `{"items", "total", "page", "page_size", "pages"}`.
* Errors always look like:

```json
{"error": {"code": "NOT_FOUND", "message": "Product not found", "details": null}}
```

Codes: `BAD_REQUEST` 400, `UNAUTHORIZED` 401, `FORBIDDEN` 403, `NOT_FOUND` 404, `CONFLICT` / `INSUFFICIENT_STOCK` 409,
`PAYLOAD_TOO_LARGE` 413, `VALIDATION_ERROR` 422, `RATE_LIMITED` 429, `INTERNAL_ERROR` 500 (no stack traces).

### Endpoint overview

| Area | Endpoints |
|---|---|
| Health | `GET /health` |
| Auth | `POST /auth/register` · `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` · `POST /auth/forgot-password` · `POST /auth/reset-password` |
| Users | `GET/POST /users` · `GET/PATCH /users/{id}` |
| Company | `GET/PATCH /companies/me` · `GET /companies/me/audit-logs` |
| Categories | `GET/POST /categories` · `GET/PATCH/DELETE /categories/{id}` |
| Products | `GET/POST /products` · `GET/PATCH/DELETE /products/{id}` |
| Batches | `GET/POST /batches` · `GET/PATCH/DELETE /batches/{id}` |
| Barcode | `GET /barcode/lookup/{barcode}` · `POST /barcode/lookup` |
| Imports | `POST /imports/csv` · `POST /imports/excel` · `GET /imports` · `GET /imports/{id}` · `GET /imports/{id}/errors` |
| Inventory | `GET /inventory` · `GET /inventory/summary` · `POST /inventory/adjust` · `GET /inventory/movements` |
| Sales | `GET/POST /sales` · `GET /sales/{id}` · `GET /sales/summary` · `GET /sales/trends` |
| Expiry | `GET /expiry` · `GET /expiry/expired` · `GET /expiry/critical` · `GET /expiry/soon` · `POST /expiry/check` |
| Alerts | `GET /alerts` · `GET /alerts/critical` · `GET /alerts/expired` · `GET /alerts/expiring-soon` · `GET /alerts/{id}` · `PATCH /alerts/{id}/read` · `PATCH /alerts/{id}/resolve` · `POST /alerts/test-sms` |
| Notifications | `GET /notifications` · `GET /notifications/{id}` · `POST /notifications/{id}/retry` |
| Analytics | `GET /analytics/overview` · `/sales` · `/inventory` · `/products` · `/trends` · `/anomalies` |
| Dashboard | `GET /dashboard/summary` · `/alerts` · `/sales` · `/inventory` · `/insights` |
| AI insights | `POST /insights/generate` · `GET /insights` · `GET /insights/{id}` · `GET /insights/recommendations` |
| AI chat | `POST /ai/chat` · `GET /ai/conversations` · `GET/DELETE /ai/conversations/{id}` · `GET /ai/status` · `GET /ai/context` |

## Authentication & roles

1. `POST /auth/register` creates a **company and its first ADMIN** and returns a token.
2. `POST /auth/login` → `{"access_token": "...", "token_type": "bearer", "expires_in": 3600, "user": {...}}`.
3. Send `Authorization: Bearer <token>`. In Swagger, click **Authorize**.
4. `POST /auth/logout` revokes the token (its `jti` is stored until expiry, then purged by the cleanup job).
5. `POST /auth/forgot-password` always answers identically (no user enumeration). The reset token is sent by SMS
   when the user has a phone number; outside production it is also returned in the response for local testing.
   Tokens are single-use: changing the password invalidates them.

Passwords: Argon2id, min 8 chars with a letter and a digit. Hashes are never returned.

| Capability | STAFF | MANAGER | ADMIN |
|---|:-:|:-:|:-:|
| Read products, batches, inventory, sales, alerts, analytics, dashboard | ✓ | ✓ | ✓ |
| Record sales, barcode lookup, mark alerts read | ✓ | ✓ | ✓ |
| Create/update products, categories, batches; adjust inventory; imports | | ✓ | ✓ |
| Run expiry check, resolve alerts, test SMS, retry notifications | | ✓ | ✓ |
| Delete products, manage users, edit company, read audit log | | | ✓ |

## Products, batches and barcodes

* A product needs a **SKU or a barcode** (both unique within the company; the same SKU can exist in another company).
  SKUs are upper-cased. Delete is a **soft delete** (`is_active=false`) so history stays intact; reactivate with
  `PATCH {"is_active": true}`.
* A product has many **batches**, each with its own `batch_number` (unique per product), optional
  `manufacturing_date`, required `expiry_date`, and quantity. Creating a batch writes a `PURCHASE` movement.
* Batch quantities never change through `PATCH /batches` — only via sales and `/inventory/adjust`, so every change
  is in the ledger. Batches with sales cannot be deleted.

### Barcode workflow

The scanner (admin/frontend) reads the barcode and sends the **string** to the backend:

```
GET  /barcode/lookup/6001234567890
POST /barcode/lookup   {"barcode": "6001234567890"}
```

A barcode identifies the **product only** — it is never parsed for batch numbers or expiry dates. The response
contains the product, its in-stock batches in FEFO order with live status and days remaining, and inventory totals
including `next_fefo_batch_id` (the batch to sell next). Unknown barcodes return `404 NOT_FOUND`.

## CSV / Excel import

`POST /imports/csv` (`.csv`) and `POST /imports/excel` (`.xlsx`, first sheet) as `multipart/form-data` field `file`.

| Column | Required | Accepted header aliases |
|---|---|---|
| `product_name` | yes | product, name, item, item_name |
| `sku` / `barcode` | at least one per row | product_code, item_code / ean, upc, gtin |
| `batch_number` | yes | batch, batch_no, lot, lot_number |
| `expiry_date` | yes | expiration_date, exp_date, expiry, best_before, use_by |
| `quantity` | yes | qty, stock, units |
| `category`, `brand`, `unit`, `description` | no | |
| `selling_price`, `cost_price` | no | price, retail_price / cost, purchase_price |
| `manufacturing_date` | no | mfg_date, production_date |

Headers are case- and spacing-insensitive. Dates: `YYYY-MM-DD` preferred; `DD/MM/YYYY`, real Excel date cells and
Excel serial numbers are accepted. Prices may include currency symbols and thousands separators.

Processing:
1. The job is recorded (`PROCESSING`) and `IMPORT_STARTED` is audited.
2. Every row is validated: required fields, dates, quantities (positive whole numbers), prices (≥ 0), identifiers,
   expiry ≥ manufacturing, no future manufacturing dates. Errors carry the **spreadsheet row number** (header = row 1).
3. Duplicates (same product + batch number earlier in the file, or already in the database) are counted separately.
4. Products are matched by SKU then barcode within the company: new ones are created, existing ones updated;
   categories are created on demand.
5. **All valid rows are written in one transaction.** Any database failure rolls everything back (status `FAILED`,
   nothing written). With `?strict=true`, any invalid row aborts the whole import.
6. New batches go straight through the expiry engine, so alerts (and SMS) are generated immediately.

Response:

```json
{"job_id": "...", "status": "COMPLETED_WITH_ERRORS", "total_rows": 8, "successful_rows": 5, "failed_rows": 2,
 "duplicate_rows": 1, "products_created": 4, "products_updated": 0, "batches_created": 5,
 "alerts_generated": 3, "errors": [{"row": 7, "field": "quantity", "value": "ten", "error_type": "INVALID",
 "message": "quantity must be a whole number"}]}
```

Full error list: `GET /imports/{id}/errors`. Sample files: `python scripts/make_sample_imports.py` writes
`scripts/sample_import.csv` and `scripts/sample_import.xlsx` with dates relative to today, including deliberate
errors and a duplicate.

## Inventory & sales

* `GET /inventory` — per product: total, sellable (non-expired), expired, batch count, nearest expiry, cost value,
  low-stock flag. `GET /inventory/summary` — units per expiry band, cost/retail value, value at risk.
* `POST /inventory/adjust` — signed `quantity_change` on one batch. `DAMAGED`, `EXPIRED`, `TRANSFER` must be
  negative; `PURCHASE`, `RETURN` positive; `ADJUSTMENT` either. Negative stock → `409 INSUFFICIENT_STOCK` unless
  `ALLOW_NEGATIVE_INVENTORY=true`.
* `GET /inventory/movements` — the immutable ledger (signed quantities, with reference to the sale/import/batch).

`POST /sales` runs, in one transaction: validate product → validate/lock batch(es) → validate quantity → decrement
stock → create sale(s) → create `SALE` movement(s) → commit.

* With `batch_id`: sells from that batch; expired batches are refused (`400`).
* Without `batch_id`: **FEFO** — allocates across non-expired batches by soonest expiry, creating one sale record
  per batch used.
* `unit_price` defaults to the product's selling price; `sold_at` defaults to now and cannot be in the future.

`GET /sales/summary` (revenue, units, transactions, average sale, estimated gross profit, top products) and
`GET /sales/trends?granularity=day|week|month&periods=N` (zero-filled series). Dates use the company timezone.

## Expiry engine

Fully deterministic — never AI:

```
days_remaining = expiry_date − today          (today in the company's timezone)

days ≤ 0                         → EXPIRED
1 … EXPIRY_CRITICAL_DAYS (30)    → CRITICAL
31 … EXPIRY_SOON_DAYS (90)       → EXPIRING_SOON
> 90                             → SAFE
remaining quantity ≤ 0           → DEPLETED
```

`POST /expiry/check` (and the daily job):
1. Retrieves the company's in-stock batches (plus empty ones not yet marked depleted).
2. Calculates days remaining and the status.
3. Updates batch statuses.
4. Creates alerts — `EXPIRED` (URGENT), `CRITICAL_EXPIRY` (CRITICAL), `EXPIRING_SOON` (WARNING).
5. Never duplicates an unresolved alert; refreshes its days/quantity instead (`duplicates_prevented`).
6. Auto-resolves superseded alerts (e.g. EXPIRING_SOON → CRITICAL) and alerts for depleted batches.
7. Commits, then sends notifications for new alerts.

```json
{"batches_checked": 6, "statuses_updated": 2, "status_counts": {"CRITICAL": 2, "SAFE": 1, ...},
 "alerts_created": 5, "duplicates_prevented": 0, "alerts_auto_resolved": 0, "sms_sent": 3, "sms_failed": 0}
```

`GET /expiry`, `/expiry/expired`, `/expiry/critical`, `/expiry/soon` classify **live**, independent of when the last
check ran.

## Alerts

Types: `EXPIRING_SOON`, `CRITICAL_EXPIRY`, `EXPIRED`, `LOW_STOCK` (alert job), plus `OVERSTOCK`, `UNUSUAL_STOCK`,
`IMPORT_ERROR`, `SYSTEM_ERROR` reserved in the schema. Severities: `INFO`, `WARNING`, `CRITICAL`, `URGENT`.

Each alert records `days_remaining`, `quantity_at_risk`, `recipient_phone`, read/resolved state and SMS delivery state
(`sms_sent`, `sms_sent_at`, `sms_error`). Resolving an alert closes it; if the condition still holds, the next check
raises a fresh one.

## SMS

SMS is sent for new alerts with severity ≥ `SMS_MIN_SEVERITY` (default CRITICAL, i.e. critical and expired batches)
to **every active ADMIN and MANAGER with a phone number**, falling back to the company phone.

| Mode | Behaviour |
|---|---|
| `SMS_MODE=mock` | No network. Messages are logged (`[MOCK SMS] to=… body=…`) and kept in an in-memory outbox; delivery records get `provider=mock` and an id like `mock-3f9a…`. Full local demo. |
| `SMS_MODE=live` + `SMS_PROVIDER=africastalking` | Africa's Talking messaging API (`AT_SANDBOX=true` for the sandbox). |
| `SMS_MODE=live` + `SMS_PROVIDER=twilio` | Twilio Messages API. |

Every attempt creates/updates a `notifications` row (`status`, `attempts`, `provider_message_id`, `error_message`,
`sent_at`). On failure the **alert is preserved**, the error is stored on the alert and the notification, the failure
is logged and audited (`SMS_FAILED`), and the expiry check continues. The alert job retries failed notifications up to
`SMS_MAX_ATTEMPTS`; `POST /notifications/{id}/retry` retries one immediately.

`POST /alerts/test-sms {"phone_number": "+254712345678", "message": "hi"}` tests the configured provider.
Phone numbers must be in international (E.164) format. Credentials come only from environment variables.

## Analytics & anomaly detection

All computed with SQL/Python:

* **Overview** — inventory (products, units, value, expired/critical/expiring/safe units), sales for the period vs
  the previous period with growth %, potential waste value, open alerts.
* **Sales** — revenue, units, transactions, average daily revenue, growth, daily/weekly(12)/monthly(12) series,
  product and category breakdown with revenue share.
* **Inventory** — stock turnover (COGS over window ÷ current inventory cost; annualised), and **expiry risk**:
  each batch's projected sell-through before expiry at the product's current velocity, consuming batches in FEFO
  order; the remainder is `units_at_risk` / `value_at_risk` (potential waste).
* **Products** — per product velocity and days of cover, classified as `FAST_MOVER`, `SLOW_MOVER`,
  `HIGH_STOCK_LOW_SALES` (no sales or > 90 days cover), `LOW_STOCK_HIGH_SALES` (< 7 days cover, above-median sales),
  or `NORMAL`.
* **Trends** — zero-filled series with a 7-day moving average.

**Anomalies** (`GET /analytics/anomalies`) are explainable and each item states the observed value, the expected
value, the score and a plain-language explanation:

| Type | Rule (defaults) |
|---|---|
| `UNUSUAL_SALES_INCREASE` / `UNUSUAL_SALES_DECLINE` | Mean daily units of the last 7 days vs the prior 28: \|z\| ≥ 2.5 (z of the recent mean) **and** \|change\| ≥ 50%. Per product (≥ 1 unit/day volume) and company-wide. |
| `UNUSUALLY_HIGH_INVENTORY` | Days of cover > 180 at baseline velocity. |
| `UNUSUALLY_LOW_INVENTORY` | Days of cover < 3, or zero sellable stock for a product that normally sells. |
| `ABNORMAL_STOCK_MOVEMENT` | Non-sale outflows (adjustments, damage, write-offs, transfers) in the last 7 days > 3× baseline expectation and ≥ 10% of stock. |

Anomalies describe statistical deviations only; the API explicitly makes **no causal claims**.

## Dashboard

Frontend-ready JSON:
* `GET /dashboard/summary` — total products, total stock, expired units/batches, critical alerts, expiring soon,
  sales and revenue (today and 30 days), inventory value, top products, recent alerts.
* `GET /dashboard/alerts` — open alerts by type and severity, unread count, SMS sent/failed, recent alerts.
* `GET /dashboard/sales` — revenue today/7d/30d, growth, 30-day daily series, top products.
* `GET /dashboard/inventory` — units by status, value at risk, batches expiring in 30 days, low-stock list.

## Background jobs

APScheduler runs inside the API process when `SCHEDULER_ENABLED=true`:

| Job | Schedule | Does |
|---|---|---|
| `expiry_job` | daily `EXPIRY_JOB_HOUR:EXPIRY_JOB_MINUTE` UTC | For every company (isolated sessions): classify → update → alert → SMS → audit `EXPIRY_CHECK_RUN` with results. |
| `alert_job` | every `ALERT_JOB_INTERVAL_MINUTES` | Retry failed SMS; create/resolve LOW_STOCK alerts. |
| `cleanup_job` | daily `CLEANUP_JOB_HOUR:30` UTC | Purge expired revoked tokens and resolved alerts older than `ALERT_RETENTION_DAYS`. |

Run any job once: `python -m app.jobs.expiry_job` (or `alert_job`, `cleanup_job`). Jobs are plain async functions
taking a session factory, so moving to Celery/Redis or cron later only means calling them from a new runner. Run a
**single** API worker while the in-process scheduler is used (or disable it and schedule the CLI commands).

## Security

Argon2id hashing · JWT with expiry, `jti` revocation and tenant claim check · role-based access control ·
company isolation on every query (404 for foreign records) · Pydantic validation on all input · parameterised
SQLAlchemy queries only · upload extension/content-type/size checks plus a request-size guard · CORS allow-list ·
per-IP rate limits on auth and upload endpoints · security headers (`nosniff`, `DENY` framing, `no-referrer`) ·
request IDs · safe error envelope without stack traces · audit log · secrets as `SecretStr`, never logged or returned.

Audited actions include `USER_LOGIN`, `USER_LOGOUT`, `PRODUCT_CREATED/UPDATED/DELETED`, `BATCH_CREATED/UPDATED/DELETED`,
`INVENTORY_ADJUSTED`, `SALE_CREATED`, `IMPORT_STARTED/COMPLETED/FAILED`, `EXPIRY_CHECK_RUN`, `ALERT_CREATED`,
`ALERT_RESOLVED`, `SMS_SENT`, `SMS_FAILED`, plus user/company/password events. Read them at
`GET /companies/me/audit-logs`.

## Testing

Tests need PostgreSQL. They create the schema in `TEST_DATABASE_URL` and truncate every table before each test.

```bash
docker compose up -d db
export TEST_DATABASE_URL=postgresql+asyncpg://expireguard:expireguard@localhost:5432/expireguard_test
pytest                    # or: pytest tests/test_expiry.py -v
```

Coverage: authentication, authorization, company isolation, products, categories, batches, inventory, sales (FEFO,
locking rules), CSV import, Excel import, barcode lookup, expiry (boundaries **0, 1, 30, 31, 90, 91** days and
custom thresholds), alerts, duplicate prevention, escalation and auto-resolution, mock SMS, SMS failure and retry,
analytics, anomalies, dashboard, background jobs, audit log, and an end-to-end seed → check → alert → SMS →
analytics → dashboard run.

## Demo workflow

```bash
docker compose up --build -d
docker compose exec backend python scripts/seed_demo_data.py        # creates "Demo Fresh Mart"
```

Logins (password `DemoPass123`): `admin@demo.expireguard.local`, `manager@demo.expireguard.local`,
`staff@demo.expireguard.local`. The demo includes a milk batch **expiring tomorrow**, expired yoghurt and beans,
fast movers (milk, bread, water, chocolate), slow/high-stock items (cheddar, energy drink, vitamin C), a sales spike
(butter), a sales decline (croissants) and a low-stock/high-sales item (infant formula), with 90 days of history.

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@demo.expireguard.local","password":"DemoPass123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
H="Authorization: Bearer $TOKEN"

curl -s -X POST localhost:8000/expiry/check -H "$H"          # classify, alert, send mock SMS
docker compose logs backend | grep "MOCK SMS"                 # see the SMS that were "sent"
curl -s localhost:8000/alerts/critical -H "$H"
curl -s localhost:8000/notifications -H "$H"
curl -s localhost:8000/analytics/overview -H "$H"
curl -s localhost:8000/analytics/anomalies -H "$H"
curl -s localhost:8000/dashboard/summary -H "$H"
curl -s localhost:8000/barcode/lookup/6001000000011 -H "$H"    # Fresh Milk 1L
```

Or seed and check in one step: `python scripts/seed_demo_data.py --run-expiry`. Re-running the seed recreates the
demo company; `--no-reset` refuses instead.

## Design notes

Deliberate choices beyond the original specification:
* `company_id` is denormalised onto `batches` and `inventory_movements` for cheap, explicit tenant isolation.
* `notifications` has `message`, `provider` and `attempts` (needed for retries); `import_jobs` has `summary`
  and `created_by`; `revoked_tokens` supports logout.
* Creating a batch sets its status immediately; alerts come from the expiry check (or immediately after an import).
* Rate limiting and the mock SMS outbox are in-process; use Redis-backed equivalents when scaling out.
* Phase 2 (AI insights) will consume these services and analytics outputs; nothing in Phase 1 depends on it.

---

## AI intelligence layer (Phase 2)

ExpireGuard is not a chatbot. The AI sits at the end of a deterministic pipeline:

```
PostgreSQL → analytics → pattern detection → anomaly detection → context builder
          → AI provider (mock | OpenAI | Anthropic) → Pydantic validation → guardrails
          → stored insight / recommendation / chat answer → dashboard & chat
```

### Components (`app/ai/`)

| File | Role |
|---|---|
| `base.py` | `AIProvider` interface: `generate_response(context, prompt, *, system, task, history)`; `AIProviderError`. |
| `providers/mock_provider.py` | Deterministic answers built only from the context. No API key needed. Also the grounded fallback. |
| `providers/openai_provider.py` | OpenAI Chat Completions over HTTPS (JSON mode). |
| `providers/anthropic_provider.py` | Anthropic Messages API over HTTPS. |
| `factory.py` | Picks the provider from `AI_PROVIDER`; falls back to mock (with a logged reason) if key or model is missing. |
| `context_builder.py` | Company-scoped, intent-scoped verified context from analytics, anomalies and alerts; deterministic intent detection and pattern detection. |
| `prompts.py` | All prompts: system rules, insights, anomaly explanation, recommendations, chat. |
| `guardrails.py` | JSON parsing, schema validation, number/date/evidence/id checks, causation and autonomy checks. |
| `insight_generator.py` | Context → AI → validate each insight → store only valid ones. |
| `recommendation_generator.py` | Context + recent insights → AI → validated recommendations (rule-based fallback). |
| `conversation.py` | Chat flow and conversation storage. |

### The verified context

The AI never receives the database or the ability to query it. For each request the context builder takes the
authenticated user's company, selects only the sections the task needs (e.g. an expiry question gets inventory,
expiry risks and alerts — not sales), calls the Phase 1 analytics/anomaly services, detects patterns and trims
lists to `AI_CONTEXT_LIST_LIMIT`. Sections: `business`, `analysis_period`, `thresholds`, `inventory`,
`expiry_risks`, `expired_batches`, `waste_risk`, `sales`, `product_performance`, `patterns`, `stock_risks`,
`anomalies`, `alerts`, `data_quality`.

Deterministic patterns: `HIGH_STOCK_LOW_SALES`, `HIGH_STOCK_LOW_SALES_SHORT_EXPIRY` (high stock, low sales and
a batch expiring within the expiring-soon window), `LOW_STOCK_HIGH_SALES`, `MULTIPLE_BATCHES_NEAR_EXPIRY`,
`FAST_MOVER`, `SLOW_MOVER`.

Inspect exactly what the AI receives: `GET /ai/context?intent=PRIORITIES` (MANAGER/ADMIN).

### Guardrails

Every AI output is parsed as JSON and validated against strict Pydantic schemas (extra keys rejected; insights
and recommendations need at least one evidence item). Then:

* **Numbers** — every number in the text must exist in the context (rounding to the written precision allowed;
  sign ignored, so "fell 8.2%" matches `-8.19`). Numbers typed by the user are *not* accepted as facts.
* **Dates** — every ISO date must exist in the context.
* **Evidence** — each item's `source` path (e.g. `expiry_risks[0].units_at_risk`) must resolve in the context
  and its `value` must match.
* **IDs** — `product_id` / `batch_id` must come from the context.
* **Causation** — sentences about sales/demand changes, or naming external factors (inflation, competitors,
  weather, holidays...), must be hedged ("may", "the data does not establish...").
* **Autonomy** — text claiming an action was taken ("I have discounted...") is rejected.

Invalid insights are **never stored**; the response lists them under `rejected` with reasons, and an
`AI_OUTPUT_REJECTED` audit entry is written. Invalid chat answers are replaced by a deterministic grounded answer
(`fallback_used: true`); recommendations fall back to rule-based ones the same way.

### Endpoints

| Endpoint | Role | Purpose |
|---|---|---|
| `POST /insights/generate` `{"categories": [...], "period_days": 30}` | MANAGER | Generate and store insights (EXPIRY, INVENTORY, SALES, PRODUCT_PERFORMANCE, ANOMALY, WASTE_RISK, STOCK_RISK). |
| `GET /insights` | STAFF | Stored insights; filter by category, severity, product. |
| `GET /insights/{id}` | STAFF | One insight with evidence and guardrail record. |
| `GET /insights/recommendations` | MANAGER | Prioritised suggestions (HIGH/MEDIUM/LOW) with evidence. |
| `POST /ai/chat` `{"message": "...", "conversation_id": null}` | STAFF | Grounded answer, intent, evidence, `grounded`, `fallback_used`. |
| `GET /ai/conversations`, `GET/DELETE /ai/conversations/{id}` | owner | Conversation history (only the owning user). |
| `GET /ai/status` | STAFF | Active provider/model (never credentials). |
| `GET /ai/context?intent=` | MANAGER | The verified context for an intent. |
| `GET /dashboard/insights` | STAFF | Insight counts and latest insights (last 7 days). |

Chat intents (keyword-based, deterministic): `EXPIRY_RISK`, `SALES_TREND`, `SLOW_MOVERS`, `HIGH_STOCK_LOW_SALES`,
`LOW_STOCK`, `ANOMALIES`, `INVENTORY_OVERVIEW`, `PRIORITIES`, `GENERAL`. Example questions: "Which products are at
highest expiry risk?", "Why are sales declining?" (answers with the observed change and states that the data does
not establish a cause), "What should I prioritize today?".

### Providers and configuration

```env
AI_PROVIDER=mock            # works offline, deterministic, used by tests and CI
AI_PROVIDER=openai          # also set OPENAI_API_KEY and OPENAI_MODEL
AI_PROVIDER=anthropic       # also set ANTHROPIC_API_KEY and ANTHROPIC_MODEL
```

If the key or model is missing the mock provider is used and `GET /ai/status` shows `fallback_reason`. Keys are
`SecretStr` values, never logged and never returned. A provider outage returns `503 AI_UNAVAILABLE` for insight
generation and a grounded fallback for chat and recommendations.

### AI demo

```bash
python scripts/seed_demo_data.py --run-expiry
# log in as admin@demo.expireguard.local / DemoPass123, then:
curl -s -X POST localhost:8000/insights/generate -H "$H" -H 'Content-Type: application/json' -d '{}'
curl -s localhost:8000/insights/recommendations -H "$H"
curl -s -X POST localhost:8000/ai/chat -H "$H" -H 'Content-Type: application/json' \
     -d '{"message": "What should I prioritize today?"}'
```

AI tests: `tests/test_ai_providers.py`, `test_ai_guardrails.py`, `test_ai_context.py`, `test_ai_insights.py`,
`test_ai_chat.py`, `test_ai_integration.py` — all run with the mock provider and no credentials.
