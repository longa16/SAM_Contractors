import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import uvicorn
from pydantic import SecretStr

from app.config import Settings
from app.db.base import Base, session_factory
from app.db.models import Company, Opportunity, User
from app.llm.client import Completion
from app.main import create_app
from app.repositories.opportunities import upsert
from app.sam.normalize import format_opp
from app.schemas.company import CompanyProfile
from app.services.auth import passwords
from app.services.screening import ScreeningService

settings = Settings(
    _env_file=None,
    database_url="sqlite:///"
    + str(Path(tempfile.mkdtemp(prefix="govcontractai-browser-")) / "check.db"),
    jwt_secret=SecretStr("browser-test-only-signing-key-with-32-characters"),
    sam_api_key=None,
    groq_api_key=None,
)
app = create_app(settings)
Base.metadata.create_all(app.state.engine)
profile = CompanyProfile.model_validate(
    {
        "company": {"legal_name": "Entreprise de test", "small_business": True},
        "targeting": {
            "naics_primary": ["238220"],
            "service_states": ["VA"],
            "contract_value_max_usd": 5000000,
            "max_bonding_usd": 100000,
        },
    }
)
with session_factory(app.state.engine).begin() as session:
    company = Company(name="Entreprise de test", profile=profile.model_dump())
    session.add(company)
    session.flush()
    session.add(
        User(
            email="reviewer@example.test",
            password_hash=passwords.hash("browser-test-password-123"),
            role="business_developer",
            company_id=company.id,
        )
    )
    session.add(
        User(
            email="admin@example.test",
            password_hash=passwords.hash("browser-admin-password-123"),
            role="admin",
            company_id=None,
        )
    )
    upsert(
        session,
        format_opp(
            {
                "noticeId": "browser-opportunity",
                "title": "Services techniques — Opportunité de test",
                "type": "Solicitation",
                "active": "Yes",
                "postedDate": "2026-10-17",
                "responseDeadLine": "2099-01-01",
                "naicsCode": "238220",
                "typeOfSetAside": "SBA",
                "officeAddress": "Richmond, VA",
            }
        ),
    )
    session.get_one(
        Opportunity, "browser-opportunity"
    ).description_text = "Technical services required."


class FakeLLM:
    def complete(self, *args, **kwargs):
        return Completion(
            '{"required_licenses":null,"required_insurance":null,"bonding_required_usd":null,"subcontracting_constraints":null,"estimated_value_usd":null,"scope_complexity":"low","risks":[],"positive_factors":[],"blocking_factors":[],"capability_keywords_matched":[]}',
            10,
            5,
        )


app.state.screening = ScreeningService(app.state.engine, settings, FakeLLM())
uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False)
