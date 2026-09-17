# Customer Transactions Ledger

A small FastAPI + PostgreSQL app that turns the legacy `Customer Transactions_114458.csv`
and `Supplier Ledger_114158.csv` exports into a queryable ledger with a web
UI: customer statements and supplier statements (each with running
balances), searchable/filterable transaction logs for both, and a dashboard
with clearly separated customer/supplier sections — all behind a login.

## Architecture

- **Database**: PostgreSQL, two independent sides:
  - Customers: `customers` (natural key = the legacy `CustomerID` code) and
    `transactions` (FK to `customers`). `amount_dr` increases what a
    customer owes us, `amount_cr` reduces it.
  - Suppliers: `suppliers` (code generated from the supplier name — the
    source file has no separate code column) and `supplier_transactions`
    (FK to `suppliers`). `amount_dr` is goods/value received (increases what
    *we* owe *them*), `amount_cr` is a payment we made (reduces it) — the
    inverse relationship to the customer side.
  - Plus `users` (login accounts). Every balance is derived
    (`sum(amount_dr) - sum(amount_cr)`), not stored, so it's always
    consistent.
- **Backend**: FastAPI + SQLAlchemy. A JSON API under `/api/*` (customers,
  transactions, dashboard) plus server-rendered HTML pages (Jinja2 +
  HTMX + Bootstrap) for the UI — no separate frontend build/toolchain.
- **Auth**: signed-cookie sessions (`starlette.SessionMiddleware`). A
  middleware (`app/auth.py`) blocks every route except `/login`, `/static/*`
  and `/healthz` unless the session has a logged-in user — this covers the
  JSON API too, not just the HTML pages.
- **Import**: `scripts/import_csv.py` (customers) and
  `scripts/import_supplier_csv.py` (suppliers) are one-off/idempotent
  loaders that clean their respective raw export (trim whitespace, turn
  placeholder cells into NULL, parse dates/decimals) and bulk-insert it.
  They write to entirely separate tables, so running one never touches the
  other's data.
- **PDF export**: `app/pdf.py` renders a customer's or supplier's full
  ledger (respecting any date/type filter applied on screen) to a paginated
  PDF with ReportLab — a repeating header (company name, "Customer Ledger"
  or "Supplier Ledger", the party, generated timestamp) and a "Page X/Y"
  footer on every page, followed by a closing balance statement ("The
  customer X is owing us: ..." / "... is owing the supplier X: ..."
  depending on which way the balance runs). "Download / Print PDF" on a
  customer's or supplier's page opens it in a new tab, from which the
  browser's PDF viewer can print or save it.

```text
app/
  main.py            FastAPI app wiring, session/auth middleware, admin bootstrap
  auth.py            AuthMiddleware — gates every route behind login
  security.py        Password hashing (PBKDF2-HMAC-SHA256)
  pdf.py             Renders a customer's or supplier's ledger to a paginated PDF (ReportLab)
  database.py        SQLAlchemy engine/session
  models.py          User, Customer, Transaction, Supplier, SupplierTransaction ORM models
  schemas.py         Pydantic request/response models
  crud.py            Query layer shared by API + pages (customer and supplier sides)
  routers/
    auth.py          /login, /logout
    customers.py     /api/customers
    transactions.py  /api/transactions
    dashboard.py     /api/dashboard
    pages.py         HTML pages (/, /customers, /transactions, /suppliers, /supplier-transactions, ...)
  templates/         Jinja2 templates (Bootstrap + HTMX, Chart.js for the dashboard)
scripts/
  import_csv.py           Loads the customer CSV into the database
  import_supplier_csv.py  Loads the supplier CSV into the database (separate tables)
  create_user.py          Creates/resets a login
Dockerfile             Builds the app image (used by the `app`, `import` and `import-suppliers` services)
docker-compose.yml     db (Postgres) + app (FastAPI) + import + import-suppliers (one-off loaders, `--profile tools`)
```

## Authentication

Every page and API route requires a logged-in session except `/login` and
static assets. There's no self-service signup — accounts are created via
the CLI or the `ADMIN_USERNAME`/`ADMIN_PASSWORD` bootstrap env vars.

- **First account**: set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env`
  before the first `docker compose up`. The app creates that user on
  startup *only if no users exist yet* — remove those two vars afterwards.
- **Add or reset a user**:

  ```bash
  docker compose run --rm -it app python -m scripts.create_user someone
  ```

  (drop `-it app` and run `python -m scripts.create_user someone` directly
  if you're running without Docker).
- **`SECRET_KEY`**: signs the session cookie. Set it to a fixed random value
  in `.env` for any real deployment — generate one with
  `python -c "import secrets; print(secrets.token_hex(32))"`. If it's left
  at the default, or changes across restarts, every user gets logged out.

## Run with Docker (recommended — works on any cloud server)

The whole stack — Postgres + the FastAPI app — is defined in
`docker-compose.yml`. `Dockerfile` builds the app image (Python 3.12-slim,
runs as a non-root user, ships with a container healthcheck).

1. **Configure**: copy `.env.example` to `.env` and set `POSTGRES_PASSWORD`,
   `SECRET_KEY`, and `ADMIN_USERNAME`/`ADMIN_PASSWORD` (see
   [Authentication](#authentication) above). Don't skip this for anything
   beyond a throwaway local test.

2. **Build and start**:

   ```bash
   docker compose up -d --build
   ```

   This starts `db` (Postgres, not exposed outside the compose network) and
   `app` (FastAPI, published on `${APP_PORT:-8000}`). The app creates its
   tables — and the admin user, if configured — on startup.

3. **Load the ledger data** (one-off; the CSVs are baked into the image):

   ```bash
   docker compose run --rm import
   docker compose run --rm import-suppliers
   ```

   These load independent tables, so run either or both, in any order.
   Data persists in the `maiders_pgdata` named volume across restarts and
   `docker compose down` (use `docker compose down -v` to also wipe it).

4. Open `http://<server-ip>:${APP_PORT:-8000}` and sign in, or `/docs` for
   the interactive API docs (also behind login).

To ship a code change: `docker compose up -d --build app`. To re-import
after replacing a CSV in the repo, rebuild first, then
`docker compose run --rm import python -m scripts.import_csv "Customer Transactions_114458.csv" --reset`
(or `import-suppliers` / `import_supplier_csv.py` / `"Supplier Ledger_114158.csv"` for supplier data).

On a cloud VM: install Docker + the Compose plugin, copy this repo over
(or `git clone` it), and run the same commands — no other setup needed. Put
a reverse proxy (nginx, Caddy, or your cloud provider's load balancer) in
front of `APP_PORT` for TLS if the UI needs to be public.

## Run without Docker

1. **Database**: point `DATABASE_URL` at any Postgres instance you have
   running (copy `.env.example` to `.env` and set it — see the comment
   near the bottom of that file). Also set `SECRET_KEY`.

2. **Python deps**:

   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Import the CSVs** (creates tables automatically):

   ```bash
   python -m scripts.import_csv "Customer Transactions_114458.csv"
   python -m scripts.import_supplier_csv "Supplier Ledger_114158.csv"
   ```

   Each writes to its own tables, so run either or both, in any order. Pass
   `--reset` to wipe and re-import just that side from scratch.

4. **Create a login**:

   ```bash
   python -m scripts.create_user admin
   ```

5. **Run the app**:

   ```bash
   uvicorn app.main:app --reload
   ```

   Then open <http://127.0.0.1:8000> for the UI, or
   <http://127.0.0.1:8000/docs> for the interactive API docs.

## Data notes

### Customers

- The CSV's `CustomerID` and `CustomerName` are usually identical, but
  `CustomerID` is sometimes a shortened form — it's the more stable field,
  so it's used as the customer's natural key (`customers.code`).
- `AmountDr` (debit) increases what a customer owes; `AmountCr` (credit)
  reduces it. A customer's outstanding balance is `total_dr - total_cr`.
- `FormID` categorizes the entry: `CAP` (payments), `IN` (invoices/supplies),
  `CCD` (misc account debit/credit), `NC` (opening balances).

### Suppliers

- The supplier CSV has only `SupplierName`, no separate code column, so
  `suppliers.code` is generated from the name (e.g. "YongXing Steel Company
  Ltd" → `YONGXINGSTEE`); suppliers are matched/deduped by exact
  (case-insensitive) name, same as the "add transaction" name lookup does
  for new records.
- `AmountDr` is goods/value received from the supplier (increases what *we*
  owe *them*); `AmountCr` is a payment *we* made (reduces it) — the inverse
  of the customer side, where `AmountDr` increases what the customer owes
  *us*. A supplier's outstanding balance (`total_dr - total_cr`) is
  therefore what we still owe them, not what they owe us.
- `FormID` categorizes the entry: `OST` (goods received/purchases), `CS`
  (payments made), `SN` (opening balance).
- `UnitCost` and `VehicleNo` use a literal `"0"` as their "no value"
  sentinel in the source file (verified: every such row also has no
  `ItemName`), so the importer treats `"0"` as NULL for just those two
  columns — everywhere else (amounts, ref numbers) a literal 0 is a real
  value and is kept as-is.

## Known limitation

Forms/HTMX actions (add/delete transaction) don't carry a separate CSRF
token; `SameSite=Lax` session cookies mitigate cross-site POSTs but this
hasn't been hardened further. Fine for an internal tool behind a login;
revisit before exposing it broadly on the public internet.
