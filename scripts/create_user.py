"""
Create or update a login for the ledger UI/API.

Usage:
    python -m scripts.create_user <username>              # prompts for password
    python -m scripts.create_user <username> --password 'x'  # non-interactive

Re-running with an existing username resets that user's password.
"""

import argparse
import getpass

from app.database import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--password", help="Skip the interactive prompt")
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
            db.commit()
            print(f"Updated password for '{username}'.")
        else:
            db.add(User(username=username, password_hash=hash_password(password)))
            db.commit()
            print(f"Created user '{username}'.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
