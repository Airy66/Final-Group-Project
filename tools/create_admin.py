"""Explicit administrator bootstrap command.

Usage:
    python -m tools.create_admin --email admin@example.com

The password may be supplied with --password or PRECISION_ADMIN_PASSWORD.
"""

import argparse
import os

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

from services.database import MongoRepository


DISALLOWED_PASSWORDS = {
    "admin123!",
    "administrator",
    "changeme",
    "change-me",
    "password",
    "password123",
}


def validate_admin_password(password):
    value = str(password or "")
    if len(value) < 12:
        raise ValueError("Administrator password must contain at least 12 characters.")
    if value.strip().lower() in DISALLOWED_PASSWORDS:
        raise ValueError("A public or default administrator password is not permitted.")
    return value


def bootstrap_admin(repository, email, password, display_name="Administrator", reset_password=False):
    normalized_email = str(email or "").strip().lower()
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("A valid administrator email is required.")
    password = validate_admin_password(password)
    existing = repository.get_user_by_email(normalized_email)
    if existing:
        if "administrator" not in (existing.get("roles") or [existing.get("role")]):
            raise ValueError("The email already belongs to a non-administrator account.")
        if not reset_password:
            raise ValueError("Administrator already exists; use --reset-password to replace its password.")
        repository.update_user(existing["_id"], {"password_hash": generate_password_hash(password)})
        return repository.get_user_by_email(normalized_email), "reset"
    user = repository.create_user(
        display_name,
        normalized_email,
        generate_password_hash(password),
        "administrator",
        account_status="active",
        roles=["administrator"],
        active_role="administrator",
        plan="professional",
        membership_tier="professional",
    )
    return user, "created"


def main(argv=None):
    load_dotenv()
    parser = argparse.ArgumentParser(description="Create or explicitly reset a Precision Curator administrator.")
    parser.add_argument("--email", default=os.getenv("PRECISION_ADMIN_EMAIL"))
    parser.add_argument("--password", default=os.getenv("PRECISION_ADMIN_PASSWORD"))
    parser.add_argument("--display-name", default=os.getenv("PRECISION_ADMIN_DISPLAY_NAME", "Administrator"))
    parser.add_argument("--reset-password", action="store_true")
    args = parser.parse_args(argv)
    if not args.email or not args.password:
        parser.error("--email and --password (or PRECISION_ADMIN_EMAIL/PRECISION_ADMIN_PASSWORD) are required")
    repository = MongoRepository(allow_memory_fallback=False)
    _user, action = bootstrap_admin(repository, args.email, args.password, args.display_name, args.reset_password)
    print(f"Administrator {action} successfully.")


if __name__ == "__main__":
    main()
