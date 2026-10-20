from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.db.base import session_factory
from app.db.models import AuditLog, Company, Opportunity, User
from app.main import create_app
from app.services.auth import DUMMY_HASH, issue_token, passwords
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
    assert client.get("/api/opportunities", headers=admin).status_code == 200
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


def test_global_catalog_exposes_no_private_company_data(api_context):
    client, headers, second = api_context
    alice, admin = headers["alice"], headers["admin"]
    client.post("/api/opportunities/test-1/screening", headers=alice)
    client.post(
        "/api/opportunities/test-1/decision",
        headers=alice,
        json={"decision": "no_bid", "reason": "Private business reason"},
    )
    client.post(
        "/api/opportunities/test-1/comments", headers=alice, json={"body": "Private discussion"}
    )
    catalog = client.get("/api/opportunities", headers=admin)
    assert catalog.status_code == 200 and catalog.json()["total"] == 1
    item = catalog.json()["items"][0]
    assert item["screening"] is None and item["status"] is None
    detail = client.get("/api/opportunities/test-1", headers=admin)
    assert detail.status_code == 200
    assert detail.json()["decision_reason"] is None
    assert "Private" not in detail.text
    assert client.get("/api/sync/status", headers=admin).json()["stages"] == {}
    assert client.get("/api/opportunities?min_score=0", headers=admin).status_code == 409
    for action, body in [
        ("screening", None),
        ("decision", {"decision": "bid"}),
        ("comments", {"body": "No company"}),
    ]:
        response = client.post(
            f"/api/opportunities/test-1/{action}", headers=admin, **({"json": body} if body else {})
        )
        assert response.status_code == 409


def test_admin_catalog_works_before_any_company_exists(engine, settings, seeded_opp):
    settings.jwt_secret = SecretStr("test-global-catalog-key-at-least-32-characters")
    with session_factory(engine).begin() as session:
        user = User(email="platform@example.test", password_hash=DUMMY_HASH, role="admin")
        session.add(user)
        session.flush()
        token = issue_token(user, settings)
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer " + token}
        assert client.get("/api/companies", headers=headers).json() == []
        response = client.get("/api/opportunities?q=Office&naics=238220", headers=headers)
        assert response.status_code == 200 and response.json()["total"] == 1
        assert client.get("/api/opportunities/test-1", headers=headers).status_code == 200
        assert client.get("/api/sync/status", headers=headers).status_code == 200


def test_company_statement_is_private_validated_and_independent(api_context, company, engine):
    client, headers, second = api_context
    path = f"/api/companies/{company.id}/statement"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=headers["alice"]).json()["statement"]["description"] == ""
    payload = {"description": "Building maintenance", "achievements": "12 completed projects"}
    assert client.put(path, headers=headers["alice"], json=payload).status_code == 200
    assert (
        client.get(path, headers=headers["admin"]).json()["statement"]["achievements"]
        == payload["achievements"]
    )
    for method in ["get", "put"]:
        args = {"json": payload} if method == "put" else {}
        assert client.request(method, path, headers=headers["bob"], **args).status_code == 404
        assert client.request(method, path, headers=headers["sub"], **args).status_code == 403
    assert (
        client.get(f"/api/companies/{second.id}/statement", headers=headers["bob"]).json()[
            "statement"
        ]["description"]
        == ""
    )
    assert client.put(path, headers=headers["ops"], json=payload).status_code == 200
    assert (
        client.put(path, headers=headers["alice"], json={"description": "x" * 10001}).status_code
        == 422
    )
    with session_factory(engine)() as session:
        saved = session.get(Company, company.id)
        assert saved.profile == company.profile
        assert saved.statement["description"] == payload["description"]
        assert (
            session.scalar(select(AuditLog).where(AuditLog.action == "company.statement_updated"))
            is not None
        )


def test_pdf_upload_download_replacement_and_company_isolation(api_context, company):
    from io import BytesIO

    from pypdf import PdfWriter

    client, headers, second = api_context
    path = f"/api/companies/{company.id}/statement/pdf"
    metadata = f"/api/companies/{company.id}/statement"
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(stream)
    raw = stream.getvalue()
    upload_headers = {**headers["alice"], "Content-Type": "application/pdf"}
    assert client.get(path, headers=headers["alice"]).status_code == 404
    assert client.put(path, headers=upload_headers, content=raw).status_code == 200
    assert client.get(metadata, headers=headers["alice"]).json()["mode"] == "pdf"
    download = client.get(path, headers=headers["admin"])
    assert download.content == raw
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["cache-control"] == "no-store"
    assert client.get(path, headers=headers["bob"]).status_code == 404
    assert (
        client.put(
            path, headers={**headers["bob"], "Content-Type": "application/pdf"}, content=raw
        ).status_code
        == 404
    )
    assert client.delete(path, headers=headers["bob"]).status_code == 404
    assert (
        client.get(f"/api/companies/{second.id}/statement/pdf", headers=headers["bob"]).status_code
        == 404
    )
    assert client.put(path, headers=headers["alice"], content=raw).status_code == 400
    for content in [b"not pdf", b"%PDF-broken", b"%PDF-" + b"x" * (5 * 1024 * 1024)]:
        assert client.put(path, headers=upload_headers, content=content).status_code == 400
    assert client.get(path, headers=headers["alice"]).content == raw
    assert (
        client.put(
            metadata, headers=headers["alice"], json={"description": "Saved form"}
        ).status_code
        == 200
    )
    meta = client.get(metadata, headers=headers["alice"]).json()
    assert meta["mode"] == "form" and meta["pdf_available"]
    assert client.post(path + "/activate", headers=headers["alice"]).status_code == 200
    assert client.delete(path, headers=headers["alice"]).status_code == 200
    meta = client.get(metadata, headers=headers["alice"]).json()
    assert meta["mode"] == "form" and not meta["pdf_available"]
    assert meta["statement"]["description"] == "Saved form"
    assert client.post(path + "/activate", headers=headers["alice"]).status_code == 404
    assert client.get(path).status_code == 401
    assert (
        client.put(
            path, headers={**headers["sub"], "Content-Type": "application/pdf"}, content=raw
        ).status_code
        == 403
    )


def test_pdf_rejects_encryption_and_empty_document(api_context, company):
    from io import BytesIO

    from pypdf import PdfWriter

    client, headers, _ = api_context
    path = f"/api/companies/{company.id}/statement/pdf"
    for encrypted in [False, True]:
        stream = BytesIO()
        writer = PdfWriter()
        if encrypted:
            writer.add_blank_page(width=612, height=792)
            writer.encrypt("secret")
        writer.write(stream)
        response = client.put(
            path,
            headers={**headers["alice"], "Content-Type": "application/pdf"},
            content=stream.getvalue(),
        )
        assert response.status_code == 400


def test_application_tracking_lifecycle_is_private_and_requires_bid(api_context, company):
    client, headers, second = api_context
    path = f"/api/companies/{company.id}/applications"
    item = path + "/test-1"
    assert client.get(path, headers=headers["alice"]).json()["total"] == 0
    assert client.post(item + "/follow", headers=headers["alice"]).json()["stage"] == "interested"
    assert (
        client.put(item, headers=headers["alice"], json={"stage": "submitted"}).status_code == 409
    )
    assert (
        client.put(
            item,
            headers=headers["alice"],
            json={"stage": "preparing", "notes": "Collecting references"},
        ).status_code
        == 200
    )
    assert (
        client.put(item, headers=headers["alice"], json={"stage": "submitted"}).status_code == 409
    )
    assert client.post(item + "/follow", headers=headers["alice"]).json()["stage"] == "preparing"
    assert (
        client.get(path, headers=headers["alice"]).json()["items"][0]["notes"]
        == "Collecting references"
    )
    assert client.get(path, headers=headers["bob"]).status_code == 404
    assert client.put(item, headers=headers["bob"], json={"stage": "interested"}).status_code == 404
    assert client.post(item + "/follow", headers=headers["bob"]).status_code == 404
    assert (
        client.get(f"/api/companies/{second.id}/applications", headers=headers["bob"]).json()[
            "items"
        ]
        == []
    )
    assert client.put(item, headers=headers["ops"], json={"stage": "preparing"}).status_code == 403
    assert client.get(path, headers=headers["ops"]).status_code == 200
    assert client.get(path, headers=headers["sub"]).status_code == 403
    assert (
        client.post("/api/opportunities/test-1/screening", headers=headers["alice"]).status_code
        == 200
    )
    assert (
        client.post(
            "/api/opportunities/test-1/decision", headers=headers["alice"], json={"decision": "bid"}
        ).status_code
        == 200
    )
    assert (
        client.put(
            item,
            headers=headers["alice"],
            json={"stage": "submitted", "notes": "Submitted on buyer portal"},
        ).status_code
        == 200
    )
    data = client.get(path, headers=headers["admin"]).json()["items"][0]
    assert data["submitted_at"] and data["stage"] == "submitted"
    assert client.put(item, headers=headers["alice"], json={"stage": "won"}).status_code == 200
    assert (
        client.put(item, headers=headers["alice"], json={"stage": "preparing"}).status_code == 409
    )
    assert client.put(item, headers=headers["alice"], json={"stage": "bogus"}).status_code == 422
    assert (
        client.put(
            item, headers=headers["alice"], json={"stage": "won", "notes": "x" * 5001}
        ).status_code
        == 422
    )
    assert client.get(path + "?page=2&page_size=1", headers=headers["alice"]).json()["items"] == []


def test_bid_decisions_appear_automatically_in_tracking(api_context, company):
    client, headers, _ = api_context
    assert (
        client.post("/api/opportunities/test-1/screening", headers=headers["alice"]).status_code
        == 200
    )
    assert (
        client.post(
            "/api/opportunities/test-1/decision", headers=headers["alice"], json={"decision": "bid"}
        ).status_code
        == 200
    )
    path = f"/api/companies/{company.id}/applications"
    item = client.get(path, headers=headers["alice"]).json()["items"][0]
    assert item["stage"] == "preparing" and item["decision"] == "bid"
