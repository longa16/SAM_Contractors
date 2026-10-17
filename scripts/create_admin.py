import argparse
import getpass
import logging

from app.config import Settings
from app.db.base import make_engine, session_factory
from app.logging_setup import setup_logging
from app.services.auth import create_user


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a platform administrator or client user")
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--role", choices=["admin", "business_developer", "ops_manager"], default="admin"
    )
    parser.add_argument("--company-id", type=int)
    args = parser.parse_args()
    password = getpass.getpass("Mot de passe (12 caractères minimum) : ")
    if password != getpass.getpass("Confirmer : "):
        raise SystemExit("Les mots de passe diffèrent.")
    settings = Settings()
    setup_logging(settings)
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine).begin() as session:
            user = create_user(
                session, args.email, password, args.role, args.company_id, actor_id="bootstrap"
            )
            logging.getLogger(__name__).info(
                "user_created", extra={"fields": {"user_id": user.id, "role": user.role}}
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
