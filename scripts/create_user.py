"""
Create or update a login for the ledger UI/API.

Usage:
    python -m scripts.create_user <username>                          # prompts for password, admin role
    python -m scripts.create_user <username> --password 'x'           # non-interactive
    python -m scripts.create_user <username> --role customer-user     # restricted role

Re-running with an existing username resets that user's password (and role,
if --role is given).
"""

import argparse
import getpass

from app.authz import ROLES
from app.database import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--password", help="Skip the interactive prompt")
    parser.add_argument(
        "--role",
        choices=ROLES,
        help="Access level to grant (default: admin for new users; unchanged for existing ones)",
    )
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if not password:
        raise SystemExit("Password cannot be empty")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        username = args.username.strip()
        user = db.query(User).filter(User.username == username).first()
        if user:
            user.password_hash = hash_password(password)
            if args.role:
                user.role = args.role
            db.commit()
            print(f"Updated '{username}' (role: {user.role}).")
        else:
            role = args.role or "admin"
            db.add(User(username=username, password_hash=hash_password(password), role=role))
            db.commit()
            print(f"Created user '{username}' (role: {role}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
