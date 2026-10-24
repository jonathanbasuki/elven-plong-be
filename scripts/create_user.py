import argparse
import asyncio
import getpass

from argon2 import PasswordHasher
from sqlalchemy import select

from plong_elven.db.models import Role, User
from plong_elven.db.session import SessionFactory


async def create_user(email: str, full_name: str, role_name: str) -> None:
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")

    async with SessionFactory() as session:
        role = await session.scalar(select(Role).where(Role.name == role_name))
        if role is None:
            raise SystemExit(f"Unknown role: {role_name}")

        existing = await session.scalar(select(User).where(User.email == email.lower()))
        if existing is not None:
            raise SystemExit("A user with that email already exists")

        session.add(
            User(
                email=email.lower(),
                full_name=full_name,
                password_hash=PasswordHasher().hash(password),
                role_id=role.id,
            )
        )
        await session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Plong Elven user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--role", choices=("floor_staff", "ops_manager"), required=True)
    args = parser.parse_args()
    asyncio.run(create_user(args.email, args.full_name, args.role))


if __name__ == "__main__":
    main()
