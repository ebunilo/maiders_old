"""Role definitions and the path rules that gate access to them.

Three roles: an admin sees everything; a customer-user only the
customer side (customers + their transactions); a supplier-user only the
supplier side (suppliers + their transactions).
"""

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


def path_allowed(role: str, path: str) -> bool:
    """Admins (or any unrecognized role prefix set) get everything; the
    other roles only get their own prefixes."""
    if role == ADMIN:
        return True
    prefixes = _ROLE_PREFIXES.get(role, ())
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def home_for(role: str) -> str:
    return ROLE_HOME.get(role, "/")
