"""Create one fictional janitorial company and its test decision-maker, once."""

import secrets
from datetime import date, timedelta

from sqlalchemy import select

from app.config import Settings
from app.db.base import make_engine, session_factory
from app.db.models import Company, User
from app.errors import Conflict
from app.schemas.company import CompanyProfile
from app.services.audit import audit
from app.services.auth import authenticate, create_user

NAME = "Potomac Facility Services LLC — Test"
EMAIL = "proposals@potomac-facility.example"


def main() -> None:
    profile = CompanyProfile.model_validate(
        {
            "company": {
                "legal_name": NAME,
                "small_business": True,
                "certifications": [],
                "sam_registration_expires": (date.today() + timedelta(days=300)).isoformat(),
            },
            "targeting": {
                "naics_primary": ["561720"],
                "naics_secondary": ["561740"],
                "psc_codes": ["S201", "S214"],
                "service_states": ["VA", "MD", "DC"],
                "min_days_to_respond": 7,
                "ideal_days": 21,
                "contract_value_min_usd": 25000,
                "contract_value_max_usd": 1500000,
                "max_bonding_usd": 500000,
            },
        }
    )
    engine = make_engine(Settings().database_url)
    password = None
    try:
        with session_factory(engine).begin() as session:
            company = session.scalar(select(Company).where(Company.name == NAME))
            existing = session.scalar(select(User).where(User.email == EMAIL))
            if existing and (company is None or existing.company_id != company.id):
                raise Conflict("L’adresse de test appartient déjà à une autre entreprise.")
            if company is None:
                company = Company(name=NAME, profile=profile.model_dump())
                session.add(company)
                session.flush()
                audit(
                    session, "company.created", "company", str(company.id), actor_id="seed-example"
                )
            if existing is None:
                password = secrets.token_urlsafe(18)
                user = create_user(
                    session,
                    EMAIL,
                    password,
                    "business_developer",
                    company.id,
                    actor_id="seed-example",
                )
                assert authenticate(session, EMAIL, password).id == user.id
            else:
                user = existing
            print(f"Entreprise : {company.name} (ID {company.id})")
            print(f"Utilisateur : {user.email} (ID {user.id}, rôle {user.role})")
        if password:
            print(f"Mot de passe généré : {password}")
        else:
            print("Déjà présent : profil et mot de passe conservés, aucun doublon créé.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
