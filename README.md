# Customer Transactions Ledger

A small FastAPI + PostgreSQL app that turns the legacy `Customer Transactions_114458.csv`
and `Supplier Ledger_114158.csv` exports into a queryable ledger with a web
UI: customer statements and supplier statements (each with running
balances), searchable/filterable transaction logs for both, and a dashboard
with clearly separated customer/supplier sections — all behind a login with
role-based access (admin / customer-user / supplier-user).

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
  JSON API too, not just the HTML pages. Each user also has a `role`
  (`app/authz.py`) that the same middleware uses to further restrict which
  paths they can reach — see [Authentication & roles](#authentication--roles).
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
  auth.py            AuthMiddleware — gates every route behind login and role
  authz.py           Role definitions (admin/customer-user/supplier-user) and their allowed paths
  security.py        Password hashing (PBKDF2-HMAC-SHA256)
  pdf.py             Renders a customer's or supplier's ledger to a paginated PDF (ReportLab)
  database.py        SQLAlchemy engine/session
  models.py          User (with role), Customer, Transaction, Supplier, SupplierTransaction ORM models
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

## Authentication & roles

Every page and API route requires a logged-in session except `/login` and
static assets. There's no self-service signup — accounts are created via
the CLI or the `ADMIN_USERNAME`/`ADMIN_PASSWORD` bootstrap env vars.

Each user also has a `role`, checked by the same middleware on every request:

| Role | Can reach | Home page |
| - | - | - |
| `admin` | Everything — dashboard, customers, transactions, suppliers, supplier transactions | `/` |
| `customer-user` | Customers + their transactions only (`/customers`, `/transactions`, and the matching `/api/*` routes) | `/customers` |
| `supplier-user` | Suppliers + their transactions only (`/suppliers`, `/supplier-transactions`) | `/suppliers` |

A request outside a role's allowed paths is redirected to that role's home
page (a 403 for API/HTMX calls) instead of erroring, and the nav bar only
shows links a role can actually open. `admin` is the default for new
accounts, so it's the one to use for anyone who should see the whole
business. Any role can delete a transaction within its own allowed paths —
`customer-user` and `supplier-user` get the delete button just like `admin`
does.

`customer-user` and `supplier-user` are also restricted to signing in
**Monday-Saturday, 8:00 AM-6:30 PM WAT** — no access at all on Sundays
(`app/authz.py`, checked against a fixed UTC+1 offset — not the host's local
clock, so it's correct regardless of the server's own timezone setting). A
login attempt outside that window is rejected with a message; a session
already open when the window closes (or Sunday begins) is logged out on its
next request. `admin` is never time- or day-restricted.

- **First account**: set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env`
  before the first `docker compose up`. The app creates that user (as
  `admin`) on startup *only if no users exist yet* — remove those two vars
  afterwards.
- **Add or reset a user**:

  ```bash
  docker compose run --rm -it app python -m scripts.create_user someone
  ```

  (drop `-it app` and run `python -m scripts.create_user someone` directly
  if you're running without Docker). This defaults new accounts to `admin`.

- **Create a restricted account**, pass `--role`:

  ```bash
  docker compose run --rm -it app python -m scripts.create_user acme_customer --role customer-user
  docker compose run --rm -it app python -m scripts.create_user acme_supplier --role supplier-user
  ```

  Re-running `create_user` on an existing username resets that account's
  password; add `--role` to also change its role (e.g. to promote someone to
  `admin`). Valid roles: `admin`, `customer-user`, `supplier-user`.

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
   [Authentication & roles](#authentication--roles) above). Don't skip this for anything
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

## Continuous deployment (GitHub Actions)

`.github/workflows/deploy.yml` deploys to the server automatically on every
push to `main` (or manually via the "Run workflow" button). It:

1. rsyncs the repo to `DEPLOY_PATH` on the server over SSH (never touching
   `.env`, which only ever lives on the server),
2. writes `.env` on the server from the GitHub secrets below — the file is
   generated in the SSH session itself, so its contents never appear on the
   runner's disk or in the workflow log,
3. runs `docker compose up -d --build app` and prunes old images.

The target server needs Docker + the Compose plugin already installed (see
[Run with Docker](#run-with-docker-recommended--works-on-any-cloud-server)
above) and `DEPLOY_PATH` already existing with this repo's `docker-compose.yml`
in it (an initial `git clone` or `rsync` to create it) — the workflow updates
that checkout, it doesn't provision the server from scratch.

Set these as **repository secrets** (Settings → Secrets and variables →
Actions):

| Secret | Used for |
| - | - |
| `SSH_HOST` | Server hostname/IP the workflow connects to |
| `SSH_USER` | SSH user on that server |
| `SSH_PRIVATE_KEY` | Private key matching a public key in that user's `~/.ssh/authorized_keys` |
| `SSH_PORT` | SSH port, if not 22 (optional) |
| `DEPLOY_PATH` | Absolute path on the server the app lives in, e.g. `/home/deploy/maiders` |
| `POSTGRES_USER` | Written into `.env` — Postgres user for the `db` container |
| `POSTGRES_PASSWORD` | Written into `.env` — Postgres password |
| `POSTGRES_DB` | Written into `.env` — Postgres database name |
| `APP_PORT` | Written into `.env` — host port the app is published on (optional, defaults to 8000) |
| `WEB_CONCURRENCY` | Written into `.env` — uvicorn worker count (optional) |
| `SECRET_KEY` | Written into `.env` — signs session cookies; generate with `python -c "import secrets; print(secrets.token_hex(32))"` and never change it across deploys, or every user gets logged out |
| `ADMIN_USERNAME` | Written into `.env` — bootstraps the first admin account (only takes effect if no users exist yet; safe to leave set) |
| `ADMIN_PASSWORD` | Written into `.env` — password for that bootstrap account |

`SSH_PRIVATE_KEY` is the one secret worth extra care: generate a dedicated
deploy keypair (`ssh-keygen -t ed25519 -f deploy_key -N ""`), put
`deploy_key.pub` in the server user's `authorized_keys`, and store
`deploy_key`'s contents (the private half) as the secret — don't reuse a
personal SSH key.

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
