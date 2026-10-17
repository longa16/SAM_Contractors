from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.db.base import session_factory
from app.db.models import AuditLog, Company, Opportunity, User
from app.main import create_app
from app.services.auth import issue_token, passwords
from app.services.screening import ScreeningService


@pytest.fixture
def api_context(engine, settings, company, seeded_opp, fake_llm):
    settings.jwt_secret = SecretStr("integration-only-jwt-key-with-at-least-32-characters")
    with session_factory(engine).begin() as session:
        second = Company(name="Second enterprise", profile=company.profile)
        session.add(second)
        session.flush()
        users = {}
        test_hash = passwords.hash("integration-password-123")
        for name, role, company_id in [
            ("admin", "admin", None),
            ("alice", "business_developer", company.id),
            ("bob", "business_developer", second.id),
            ("ops", "ops_manager", company.id),
            ("sub", "subcontractor", company.id),
        ]:
            user = User(
                email=f"{name}@example.test",
                password_hash=test_hash,
                role=role,
                company_id=company_id,
            )
            session.add(user)
            session.flush()
            users[name] = user
    app = create_app(settings)
    app.state.screening = ScreeningService(engine, settings, fake_llm)
    with TestClient(app) as client:
        yield (
            client,
            {
                name: {"Authorization": "Bearer " + issue_token(user, settings)}
                for name, user in users.items()
            },
            second,
        )


def test_login_validation_and_secret_safe_errors(api_context, settings):
    client, headers, second = api_context
    response = client.post(
        "/api/auth/login",
        json={"email": "alice@example.test", "password": "integration-password-123"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer " + token}).json()["role"]
        == "business_developer"
    )
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer bad"}).status_code == 401
    for email in ["absent@example.test", "alice@example.test"]:
        denied = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
        assert denied.status_code == 401 and "wrong-password" not in denied.text
    invalid = client.post(
        "/api/users",
        headers=headers["admin"],
        json={"email": "x", "password": "private-secret", "role": "invalid"},
    )
    assert invalid.status_code == 422 and "private-secret" not in invalid.text
    expired = jwt.encode(
        {
            "sub": "1",
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
            "iss": settings.jwt_issuer,
            "aud": "govcontractai-api",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer " + expired}).status_code
        == 401
    )


def test_private_decisions_comments_and_results_are_isolated(api_context, company):
    client, headers, second = api_context
    alice, bob = headers["alice"], headers["bob"]
    assert client.post("/api/opportunities/test-1/screening", headers=alice).json()["stage"] == 2
    assert (
        client.post(
            "/api/opportunities/test-1/comments",
            headers=alice,
            json={"body": "Private company A note"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/opportunities/test-1/decision", headers=alice, json={"decision": "bid"}
        ).json()["status"]
        == "bid"
    )
    private = client.get("/api/opportunities/test-1", headers=bob).json()
    assert private["status"] == "new" and private["screening"] is None
    assert client.get("/api/opportunities/test-1/comments", headers=bob).json() == []
    for path, method, payload in [
        (f"/api/companies/{company.id}/profile", "get", None),
        (f"/api/opportunities/test-1?company_id={company.id}", "get", None),
        (f"/api/opportunities/test-1/comments?company_id={company.id}", "get", None),
        (f"/api/opportunities/test-1/screening?company_id={company.id}", "post", None),
        (
            f"/api/opportunities/test-1/decision?company_id={company.id}",
            "post",
            {"decision": "bid"},
        ),
        (f"/api/opportunities/test-1/comments?company_id={company.id}", "post", {"body": "Attack"}),
    ]:
        assert (
            client.request(
                method, path, headers=bob, **({"json": payload} if payload else {})
            ).status_code
            == 404
        )
    assert client.get("/api/companies", headers=bob).json()[0]["id"] == second.id
    assert client.get("/api/audit", headers=bob).status_code == 403
    for path in ["/api/opportunities", "/api/opportunities/test-1", "/api/companies", "/api/audit"]:
        assert client.get(path, headers=headers["sub"]).status_code == 403
    assert (
        client.post(
            "/api/opportunities/test-1/decision", headers=headers["ops"], json={"decision": "bid"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/opportunities/test-1/decision", headers=alice, json={"decision": "bid"}
        ).status_code
        == 409
    )


def test_no_bid_needs_reason_and_records_actor(api_context, engine):
    client, headers, second = api_context
    alice = headers["alice"]
    client.post("/api/opportunities/test-1/screening", headers=alice)
    assert (
        client.post(
            "/api/opportunities/test-1/decision", headers=alice, json={"decision": "no_bid"}
        ).status_code
        == 409
    )
    response = client.post(
        "/api/opportunities/test-1/decision",
        headers=alice,
        json={"decision": "no_bid", "reason": "Capacité indisponible"},
    )
    assert response.status_code == 200 and response.json()["status"] == "no-bid"
    with session_factory(engine)() as session:
        record = session.scalar(
            select(AuditLog)
            .where(AuditLog.action == "case.transition")
            .order_by(AuditLog.id.desc())
        )
        assert record.actor_type == "user" and record.before["status"] == "screening"
        assert record.after["status"] == "no-bid"


def test_filters_profile_management_and_platform_supervision(api_context, profile, company):
    client, headers, second = api_context
    alice, admin = headers["alice"], headers["admin"]
    assert client.get("/api/opportunities?naics=111111", headers=alice).json()["total"] == 0
    assert client.get("/api/opportunities?q=Office", headers=alice).json()["total"] == 1
    assert client.get("/api/opportunities?q=%25", headers=alice).json()["total"] == 0
    assert client.get("/api/opportunities?page_size=500", headers=alice).status_code == 422
    assert (
        client.get(
            "/api/opportunities?deadline_before=2026-01-01T00:00:00", headers=alice
        ).status_code
        == 409
    )
    client.post("/api/opportunities/test-1/screening", headers=alice)
    assert (
        client.get(
            "/api/opportunities?min_score=1&status=screening&recommendation=RECOMMENDED&naics=238220&set_aside=small_business&sort=deadline",
            headers=alice,
        ).json()["total"]
        == 1
    )
    assert client.get("/api/opportunities?state=MD", headers=alice).json()["total"] == 0
    changed = profile.model_dump()
    changed["company"]["legal_name"] = "Updated enterprise"
    assert (
        client.put(f"/api/companies/{company.id}/profile", headers=alice, json=changed).status_code
        == 200
    )
    assert (
        client.post(
            "/api/opportunities/test-1/decision", headers=alice, json={"decision": "bid"}
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/api/companies/{company.id}/profile", headers=headers["ops"], json=changed
        ).status_code
        == 403
    )
    assert (
        client.post("/api/companies", headers=alice, json={"name": "Unauthorized"}).status_code
        == 403
    )
    created = client.post("/api/companies", headers=admin, json={"name": "Third enterprise"})
    assert created.status_code == 201
    new_id = created.json()["id"]
    assert client.get(f"/api/companies/{new_id}/profile", headers=admin).json()["profile"] is None
    user = client.post(
        "/api/users",
        headers=admin,
        json={
            "email": "new@example.test",
            "password": "private-test-password-123",
            "role": "business_developer",
            "company_id": new_id,
        },
    )
    assert user.status_code == 201 and "password" not in user.text
    assert (
        client.post(
            "/api/users",
            headers=admin,
            json={
                "email": "new@example.test",
                "password": "private-test-password-123",
                "role": "business_developer",
                "company_id": new_id,
            },
        ).status_code
        == 409
    )
    assert client.get("/api/opportunities", headers=admin).status_code == 409
    assert (
        client.get(f"/api/opportunities?company_id={company.id}", headers=admin).status_code == 200
    )
    assert (
        client.get(f"/api/sync/status?company_id={company.id}", headers=admin).json()["stages"][
            "stage2"
        ]
        == 1
    )
    assert (
        client.get("/api/audit?entity_type=company_case&page=1", headers=admin).status_code == 200
    )


def test_dashboard_assets_auth_route_inventory_and_disabled_company(api_context, engine, company):
    client, headers, second = api_context
    html = client.get("/")
    assert html.status_code == 200 and 'lang="fr"' in html.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200
    assert "frame-ancestors 'none'" in html.headers["Content-Security-Policy"]
    checked = 0
    for path, methods in client.app.openapi()["paths"].items():
        if not path.startswith("/api/") or path == "/api/auth/login":
            continue
        for method, operation in methods.items():
            assert operation.get("security"), (method, path)
            url = path.replace("{notice_id}", "test-1").replace("{company_id}", str(company.id))
            response = client.request(method, url)
            assert response.status_code == 401, (method, path)
            checked += 1
    assert checked >= 15
    with session_factory(engine).begin() as session:
        session.get_one(Company, company.id).is_active = False
    assert client.get("/api/auth/me", headers=headers["alice"]).status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "alice@example.test", "password": "integration-password-123"},
        ).status_code
        == 401
    )


def test_bid_rechecks_current_eligibility_after_sam_changes(api_context, engine):
    client, headers, second = api_context
    alice = headers["alice"]
    assert client.post("/api/opportunities/test-1/screening", headers=alice).status_code == 200
    with session_factory(engine).begin() as session:
        opportunity = session.get_one(Opportunity, "test-1")
        opportunity.set_aside_category, opportunity.set_aside_code = "eight_a", "8A"
    response = client.post(
        "/api/opportunities/test-1/decision", headers=alice, json={"decision": "bid"}
    )
    assert response.status_code == 409
