# Customer Transactions Ledger

A small FastAPI + PostgreSQL app that turns the legacy `Customer Transactions_114458.csv`
export into a queryable ledger with a web UI: customer statements with running
balances, a searchable/filterable transactions log, and a dashboard — all
behind a login.

## Architecture

- **Database**: PostgreSQL. `customers` (natural key = the legacy
  `CustomerID` code), `transactions` (one row per ledger entry, FK to
  `customers`), and `users` (login accounts). Balance is derived
  (`sum(amount_dr) - sum(amount_cr)`), not stored, so it's always
  consistent.
- **Backend**: FastAPI + SQLAlchemy. A JSON API under `/api/*` (customers,
  transactions, dashboard) plus server-rendered HTML pages (Jinja2 +
  HTMX + Bootstrap) for the UI — no separate frontend build/toolchain.
- **Auth**: signed-cookie sessions (`starlette.SessionMiddleware`). A
  middleware (`app/auth.py`) blocks every route except `/login`, `/static/*`
  and `/healthz` unless the session has a logged-in user — this covers the
  JSON API too, not just the HTML pages.
- **Import**: `scripts/import_csv.py` is a one-off/idempotent loader that
  cleans the raw export (trims whitespace, turns the `==========`
  placeholder cells into NULL, parses dates/decimals) and bulk-inserts it.
- **PDF export**: `app/pdf.py` renders a customer's full ledger (respecting
  any date/type filter applied on screen) to a paginated PDF with ReportLab
  — a repeating header (company name, "Customer Ledger", customer, generated
  timestamp) and a "Page X/Y" footer on every page. "Download / Print PDF"
  on a customer's page opens it in a new tab, from which the browser's PDF
  viewer can print or save it.

```text
app/
  main.py            FastAPI app wiring, session/auth middleware, admin bootstrap
  auth.py            AuthMiddleware — gates every route behind login
  security.py        Password hashing (PBKDF2-HMAC-SHA256)
  pdf.py             Renders a customer's ledger to a paginated PDF (ReportLab)
  database.py        SQLAlchemy engine/session
  models.py          User, Customer, Transaction ORM models
  schemas.py         Pydantic request/response models
  crud.py            Query layer shared by API + pages
  routers/
    auth.py          /login, /logout
    customers.py     /api/customers
    transactions.py  /api/transactions
    dashboard.py     /api/dashboard
    pages.py         HTML pages (/, /customers, /transactions, ...)
  templates/         Jinja2 templates (Bootstrap + HTMX, Chart.js for the dashboard)
scripts/
  import_csv.py      Loads the CSV into the database
  create_user.py     Creates/resets a login
Dockerfile             Builds the app image (used by both the `app` and `import` services)
docker-compose.yml     db (Postgres) + app (FastAPI) + import (one-off loader, `--profile tools`)
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

3. **Load the ledger data** (one-off; the CSV is baked into the image):

   ```bash
   docker compose run --rm import
   ```

   Data persists in the `maiders_pgdata` named volume across restarts and
   `docker compose down` (use `docker compose down -v` to also wipe it).

4. Open `http://<server-ip>:${APP_PORT:-8000}` and sign in, or `/docs` for
   the interactive API docs (also behind login).

To ship a code change: `docker compose up -d --build app`. To re-import
after replacing the CSV in the repo, rebuild first, then
`docker compose run --rm import python -m scripts.import_csv "Customer Transactions_114458.csv" --reset`.

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

3. **Import the CSV** (creates tables automatically):

   ```bash
   python -m scripts.import_csv "Customer Transactions_114458.csv"
   ```

   Pass `--reset` to wipe and re-import from scratch.

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

- The CSV's `CustomerID` and `CustomerName` are usually identical, but
  `CustomerID` is sometimes a shortened form — it's the more stable field,
  so it's used as the customer's natural key (`customers.code`).
- `AmountDr` (debit) increases what a customer owes; `AmountCr` (credit)
  reduces it. A customer's outstanding balance is `total_dr - total_cr`.
- `FormID` categorizes the entry: `CAP` (payments), `IN` (invoices/supplies),
  `CCD` (misc account debit/credit), `NC` (opening balances).

## Known limitation

Forms/HTMX actions (add/delete transaction) don't carry a separate CSRF
token; `SameSite=Lax` session cookies mitigate cross-site POSTs but this
hasn't been hardened further. Fine for an internal tool behind a login;
revisit before exposing it broadly on the public internet.
