"""Run an isolated, loopback-only demonstration without external credentials."""

import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import SecretStr

from app.config import Settings
from app.db.base import Base, session_factory
from app.db.models import Company, Opportunity
from app.llm.client import Completion
from app.main import create_app
from app.repositories.opportunities import upsert
from app.sam.normalize import format_opp
from app.schemas.company import CompanyProfile
from app.schemas.screening import ScreeningLLMOutput
from app.services.auth import create_user
from app.services.screening import ScreeningService

DEMO_PASSWORD = "Demo-test-2026!"


class DemoLLM:
    def complete(
        self,
        system: str,
        user: str,
        *,
        schema: dict[str, Any],
        temperature: float,
        max_tokens: int,
    ) -> Completion:
        output = ScreeningLLMOutput(
            required_licenses=None,
            required_insurance=None,
            bonding_required_usd=None,
            subcontracting_constraints=None,
            estimated_value_usd=None,
            scope_complexity="low",
            risks=[],
            positive_factors=[],
            blocking_factors=[],
            capability_keywords_matched=[],
        )
        return Completion(output.model_dump_json(), 0, 0)


def create_demo():
    directory = Path(tempfile.mkdtemp(prefix="govcontractai-demo-"))
    settings = Settings(
        _env_file=None,
        auto_initial_sync=False,
        mail_mode="local",
        public_app_url="http://127.0.0.1:8000",
        database_url=f"sqlite:///{directory / 'demo.db'}",
        jwt_secret=SecretStr(secrets.token_urlsafe(48)),
        sam_api_key=None,
        groq_api_key=None,
        screening_stage2_daily_cap=100,
        llm_daily_token_budget=100000000,
    )
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)
    now = datetime.now(UTC)
    profile = CompanyProfile.model_validate(
        {
            "company": {"legal_name": "Entreprise Alpha — Démo", "small_business": True},
            "targeting": {
                "naics_primary": ["238220"],
                "service_states": ["VA", "MD", "DC"],
                "contract_value_max_usd": 5000000,
                "max_bonding_usd": 100000,
            },
        }
    )
    with session_factory(app.state.engine).begin() as session:
        create_user(session, "admin@demo.test", DEMO_PASSWORD, "admin", None, actor_id="demo")
        for label in ["Alpha", "Beta"]:
            company_profile = profile.model_copy(deep=True)
            company_profile.company.legal_name = f"Entreprise {label} — Démo"
            company = Company(
                name=company_profile.company.legal_name, profile=company_profile.model_dump()
            )
            session.add(company)
            session.flush()
            create_user(
                session,
                f"{label.lower()}@demo.test",
                DEMO_PASSWORD,
                "business_developer",
                company.id,
                actor_id="demo",
            )
        for number, title, restriction, days in [
            (1, "Maintenance des installations techniques — DÉMO", "SBA", 30),
            (2, "Rénovation des réseaux de plomberie — DÉMO", "NONE", 21),
            (3, "Marché réservé 8(a) — DÉMO", "8A", 30),
            (4, "Intervention urgente — DÉMO", "NONE", 1),
        ]:
            notice_id = f"demo-{number}"
            upsert(
                session,
                format_opp(
                    {
                        "noticeId": notice_id,
                        "title": title,
                        "type": "Solicitation",
                        "active": "Yes",
                        "postedDate": now.date().isoformat(),
                        "responseDeadLine": (now + timedelta(days=days)).isoformat(),
                        "naicsCode": "238220",
                        "typeOfSetAside": restriction,
                        "officeAddress": "Richmond, VA",
                        "fullParentPathName": "Agence fictive.Démonstration",
                    },
                    now,
                ),
            )
            session.get_one(Opportunity, notice_id).description_text = (
                "Fictional demonstration: technical maintenance services. "
                "This is not a real government notice."
            )
    app.state.screening = ScreeningService(app.state.engine, settings, DemoLLM())
    app.state.screening.batch()
    return app


def main() -> None:
    uvicorn.run(create_demo(), host="127.0.0.1", port=8000, access_log=False)


if __name__ == "__main__":
    main()
