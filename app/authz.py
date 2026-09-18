"""Role definitions and the path rules that gate access to them.

Three roles: an admin sees everything; a customer-user only the
customer side (customers + their transactions); a supplier-user only the
supplier side (suppliers + their transactions).
"""

import datetime

ADMIN = "admin"
CUSTOMER_USER = "customer-user"
SUPPLIER_USER = "supplier-user"
ROLES = (ADMIN, CUSTOMER_USER, SUPPLIER_USER)

ROLE_HOME = {
    ADMIN: "/",
    CUSTOMER_USER: "/customers",
    SUPPLIER_USER: "/suppliers",
}

_CUSTOMER_PREFIXES = ("/customers", "/transactions", "/api/customers", "/api/transactions")
_SUPPLIER_PREFIXES = ("/suppliers", "/supplier-transactions")

_ROLE_PREFIXES = {
    CUSTOMER_USER: _CUSTOMER_PREFIXES,
    SUPPLIER_USER: _SUPPLIER_PREFIXES,
}

# Creating a customer record is open to every role, including
# supplier-user, which otherwise has no access to the customer prefixes
# at all (the JSON endpoint plus the HTMX form page it powers).
_CUSTOMER_CREATE_PATHS = ("/api/customers", "/customers/new")


def path_allowed(role: str, path: str) -> bool:
    """Admins (or any unrecognized role prefix set) get everything; the
    other roles only get their own prefixes. Any role may create a new
    customer."""
    if path in _CUSTOMER_CREATE_PATHS:
        return True
    if role == ADMIN:
        return True
    prefixes = _ROLE_PREFIXES.get(role, ())
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def home_for(role: str) -> str:
    return ROLE_HOME.get(role, "/")


# West Africa Time is a fixed UTC+1 offset year-round (no DST), so a plain
# timezone() covers it correctly without depending on the host's local
# clock/TZ setting (fragile — breaks the moment the app moves host, or runs
# in a container whose TZ wasn't set) or on a tz database being installed
# (zoneinfo("Africa/Lagos") can fail on a minimal image without tzdata).
WAT = datetime.timezone(datetime.timedelta(hours=1), name="WAT")

LOGIN_WINDOW_START = datetime.time(8, 0)
LOGIN_WINDOW_END = datetime.time(18, 30)
LOGIN_CLOSED_WEEKDAY = 6  # datetime.weekday(): Monday=0 ... Sunday=6
LOGIN_WINDOW_MESSAGE = (
    "Your account may only be used Monday-Saturday, between 8:00 AM and "
    "6:30 PM (WAT). Please sign in again during that window."
)


def login_restricted(role: str, now: datetime.datetime | None = None) -> bool:
    """Non-admin roles are time-boxed to the official login window
    (Mon-Sat, 8:00 AM-6:30 PM WAT) and shut out entirely on Sundays;
    admins are never restricted."""
    if role == ADMIN:
        return False
    current = (now or datetime.datetime.now(WAT)).astimezone(WAT)
    if current.weekday() == LOGIN_CLOSED_WEEKDAY:
        return True
    return not (LOGIN_WINDOW_START <= current.time() <= LOGIN_WINDOW_END)
