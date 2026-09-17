# Customer Transactions Ledger

A small FastAPI + PostgreSQL app that turns the legacy `Customer Transactions_114458.csv`
export into a queryable ledger with a web UI: customer statements with running
balances, a searchable/filterable transactions log, and a dashboard.

## Architecture

- **Database**: PostgreSQL, two tables — `customers` (natural key = the
  legacy `CustomerID` code) and `transactions` (one row per ledger entry,
  FK to `customers`). Balance is derived (`sum(amount_dr) - sum(amount_cr)`),
  not stored, so it's always consistent.
- **Backend**: FastAPI + SQLAlchemy. A JSON API under `/api/*` (customers,
  transactions, dashboard) plus server-rendered HTML pages (Jinja2 +
  HTMX + Bootstrap) for the UI — no separate frontend build/toolchain.
- **Import**: `scripts/import_csv.py` is a one-off/idempotent loader that
  cleans the raw export (trims whitespace, turns the `==========`
  placeholder cells into NULL, parses dates/decimals) and bulk-inserts it.

```
app/
  main.py            FastAPI app wiring
  database.py        SQLAlchemy engine/session
  models.py          Customer, Transaction ORM models
  schemas.py         Pydantic request/response models
  crud.py            Query layer shared by API + pages
  routers/
    customers.py     /api/customers
    transactions.py  /api/transactions
    dashboard.py     /api/dashboard
    pages.py         HTML pages (/, /customers, /transactions, ...)
  templates/         Jinja2 templates (Bootstrap + HTMX, Chart.js for the dashboard)
scripts/import_csv.py
Dockerfile             Builds the app image (used by both the `app` and `import` services)
docker-compose.yml     db (Postgres) + app (FastAPI) + import (one-off loader, `--profile tools`)
```

## Run with Docker (recommended — works on any cloud server)

The whole stack — Postgres + the FastAPI app — is defined in
`docker-compose.yml`. `Dockerfile` builds the app image (Python 3.12-slim,
runs as a non-root user, ships with a container healthcheck).

1. **Configure** (optional): copy `.env.example` to `.env` and set your own
   `POSTGRES_PASSWORD` and `APP_PORT`. Defaults work fine for a quick test.

2. **Build and start**:

   ```
   docker compose up -d --build
   ```

   This starts `db` (Postgres, not exposed outside the compose network) and
   `app` (FastAPI, published on `${APP_PORT:-8000}`). The app creates its
   tables on startup but the database starts empty.

3. **Load the ledger data** (one-off; the CSV is baked into the image):

   ```
   docker compose run --rm import
   ```

   Data persists in the `maiders_pgdata` named volume across restarts and
   `docker compose down` (use `docker compose down -v` to also wipe it).

4. Open `http://<server-ip>:${APP_PORT:-8000}` for the UI, or
   `/docs` for the interactive API docs.

To ship a code change: `docker compose up -d --build app`. To re-import
after replacing the CSV in the repo, rebuild first, then
`docker compose run --rm import python -m scripts.import_csv "Customer Transactions_114458.csv" --reset`.

On a cloud VM: install Docker + the Compose plugin, copy this repo over
(or `git clone` it), and run the same three commands — no other setup
needed. Put a reverse proxy (nginx, Caddy, or your cloud provider's load
balancer) in front of `APP_PORT` for TLS if the UI needs to be public.

## Run without Docker

1. **Database**: point `DATABASE_URL` at any Postgres instance you have
   running (copy `.env.example` to `.env` and set it — see the comment
   near the bottom of that file).

2. **Python deps**:

   ```
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Import the CSV** (creates tables automatically):

   ```
   python -m scripts.import_csv "Customer Transactions_114458.csv"
   ```

   Pass `--reset` to wipe and re-import from scratch.

4. **Run the app**:

   ```
   uvicorn app.main:app --reload
   ```

   Then open http://127.0.0.1:8000 for the UI, or
   http://127.0.0.1:8000/docs for the interactive API docs.

## Data notes

- The CSV's `CustomerID` and `CustomerName` are usually identical, but
  `CustomerID` is sometimes a shortened form — it's the more stable field,
  so it's used as the customer's natural key (`customers.code`).
- `AmountDr` (debit) increases what a customer owes; `AmountCr` (credit)
  reduces it. A customer's outstanding balance is `total_dr - total_cr`.
- `FormID` categorizes the entry: `CAP` (payments), `IN` (invoices/supplies),
  `CCD` (misc account debit/credit), `NC` (opening balances).
