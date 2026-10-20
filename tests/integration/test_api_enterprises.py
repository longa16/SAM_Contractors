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
    bob = headers["bob"]
    assert (
        client.post(
            f"/api/opportunities/test-1/screening?company_id={company.id}", headers=headers["admin"]
        ).json()["stage"]
        == 2
    )
    assert (
        client.post(
            f"/api/opportunities/test-1/comments?company_id={company.id}",
            headers=headers["admin"],
            json={"body": "Private company A note"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/opportunities/test-1/decision?company_id={company.id}",
            headers=headers["admin"],
            json={"decision": "bid"},
        ).json()["status"]
        == "bid"
    )
    private = client.get("/api/opportunities/test-1", headers=bob).json()
    assert private["status"] == "new" and private["screening"] is None
    assert client.get("/api/opportunities/test-1/comments", headers=bob).status_code == 403
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
        assert client.request(
            method, path, headers=bob, **({"json": payload} if payload else {})
        ).status_code == (
            403 if any(part in path for part in ["/comments", "/screening", "/decision"]) else 404
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
            f"/api/opportunities/test-1/decision?company_id={company.id}",
            headers=headers["admin"],
            json={"decision": "bid"},
        ).status_code
        == 409
    )


def test_no_bid_needs_reason_and_records_actor(api_context, engine, company):
    client, headers, second = api_context
    client.post(
        f"/api/opportunities/test-1/screening?company_id={company.id}", headers=headers["admin"]
    )
    assert (
        client.post(
            f"/api/opportunities/test-1/decision?company_id={company.id}",
            headers=headers["admin"],
            json={"decision": "no_bid"},
        ).status_code
        == 409
    )
    response = client.post(
        f"/api/opportunities/test-1/decision?company_id={company.id}",
        headers=headers["admin"],
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
    client.post(
        f"/api/opportunities/test-1/screening?company_id={company.id}", headers=headers["admin"]
    )
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
            f"/api/opportunities/test-1/decision?company_id={company.id}",
            headers=headers["admin"],
            json={"decision": "bid"},
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/api/companies/{company.id}/profile", headers=headers["ops"], json=changed
        ).status_code
        == 200
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
        if not path.startswith("/api/") or path in {
            "/api/auth/login",
            "/api/auth/forgot-password",
            "/api/auth/accept-action",
        }:
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


def test_bid_rechecks_current_eligibility_after_sam_changes(api_context, engine, company):
    client, headers, second = api_context
    assert (
        client.post(
            f"/api/opportunities/test-1/screening?company_id={company.id}", headers=headers["admin"]
        ).status_code
        == 200
    )
    with session_factory(engine).begin() as session:
        opportunity = session.get_one(Opportunity, "test-1")
        opportunity.set_aside_category, opportunity.set_aside_code = "eight_a", "8A"
    response = client.post(
        f"/api/opportunities/test-1/decision?company_id={company.id}",
        headers=headers["admin"],
        json={"decision": "bid"},
    )
    assert response.status_code == 409


def test_global_catalog_exposes_no_private_company_data(api_context, company):
    client, headers, second = api_context
    admin = headers["admin"]
    client.post(
        f"/api/opportunities/test-1/screening?company_id={company.id}", headers=headers["admin"]
    )
    client.post(
        f"/api/opportunities/test-1/decision?company_id={company.id}",
        headers=headers["admin"],
        json={"decision": "no_bid", "reason": "Private business reason"},
    )
    client.post(
        f"/api/opportunities/test-1/comments?company_id={company.id}",
        headers=headers["admin"],
        json={"body": "Private discussion"},
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


def test_company_wishes_and_admin_only_workflow(api_context, company):
    client, headers, second = api_context
    path = f"/api/companies/{company.id}/applications"
    item = path + "/test-1"
    assert client.post(item + "/follow", headers=headers["alice"]).json()["stage"] == "interested"
    assert client.post(item + "/follow", headers=headers["alice"]).json()["stage"] == "interested"
    assert client.get(path, headers=headers["alice"]).json()["total"] == 1
    assert client.post(item + "/follow", headers=headers["bob"]).status_code == 404
    assert (
        client.get(f"/api/companies/{second.id}/applications", headers=headers["bob"]).json()[
            "items"
        ]
        == []
    )
    for name in ["alice", "ops", "bob"]:
        assert client.put(item, headers=headers[name], json={"notes": "tamper"}).status_code == 403
        assert (
            client.post(
                item + "/validate",
                headers=headers[name],
                json={"expected_stage": "interested", "summary": "tamper"},
            ).status_code
            == 403
        )
        assert client.get("/api/admin/applications", headers=headers[name]).status_code == 403
        for action in ["screening", "decision", "comments"]:
            assert (
                client.post(
                    f"/api/opportunities/test-1/{action}",
                    headers=headers[name],
                    json={"decision": "bid", "body": "tamper"},
                ).status_code
                == 403
            )
    assert client.get(path, headers=headers["sub"]).status_code == 403
    assert client.get("/api/admin/applications", headers=headers["admin"]).json()["total"] == 1
    assert (
        client.put(
            item, headers=headers["admin"], json={"notes": "INTERNAL confidential"}
        ).status_code
        == 200
    )
    assert "INTERNAL" not in client.get(path, headers=headers["alice"]).text
    assert "INTERNAL" in client.get(path, headers=headers["admin"]).text
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={"expected_stage": "ready", "summary": "skip"},
        ).status_code
        == 409
    )
    payload = {"expected_stage": "interested", "summary": "Votre dossier est pris en charge."}
    assert (
        client.post(item + "/validate", headers=headers["admin"], json=payload).json()["stage"]
        == "examining"
    )
    assert (
        client.post(item + "/validate", headers=headers["admin"], json=payload).status_code == 409
    )
    assert len(client.get(path, headers=headers["alice"]).json()["items"][0]["history"]) == 1
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={"expected_stage": "examining", "summary": "Exam complete"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/opportunities/test-1/screening?company_id={company.id}", headers=headers["admin"]
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/opportunities/test-1/decision?company_id={company.id}",
            headers=headers["admin"],
            json={"decision": "bid"},
        ).status_code
        == 200
    )
    for stage, next_stage in [("examining", "preparing"), ("preparing", "ready")]:
        if stage == "preparing":
            complete_case_dossier(client, headers, company.id)
        assert (
            client.post(
                item + "/validate",
                headers=headers["admin"],
                json={"expected_stage": stage, "summary": "Étape terminée et vérifiée."},
            ).json()["stage"]
            == next_stage
        )
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={"expected_stage": "ready", "summary": "Sent"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={
                "expected_stage": "ready",
                "summary": "Votre dossier a été transmis.",
                "evidence": "PRIVATE receipt 123",
            },
        ).json()["stage"]
        == "submitted"
    )
    public = client.get(path, headers=headers["alice"])
    assert public.json()["items"][0]["submitted_at"]
    assert "PRIVATE" not in public.text and "INTERNAL" not in public.text
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={
                "expected_stage": "submitted",
                "summary": "Marché attribué à votre entreprise.",
                "outcome": "won",
            },
        ).json()["stage"]
        == "won"
    )
    assert len(client.get(path, headers=headers["alice"]).json()["items"][0]["history"]) == 5
    assert client.post(item + "/follow", headers=headers["alice"]).json()["stage"] == "won"
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={"expected_stage": "won", "summary": "repeat", "outcome": "lost"},
        ).status_code
        == 409
    )
    assert client.get(path + "?page=2&page_size=1", headers=headers["alice"]).json()["items"] == []


def test_admin_can_stop_case_without_skipping_to_result(api_context, company):
    client, headers, _ = api_context
    item = f"/api/companies/{company.id}/applications/test-1"
    assert client.post(item + "/follow", headers=headers["ops"]).status_code == 200
    for payload in [
        {"expected_stage": "interested", "summary": " ", "outcome": "withdrawn"},
        {"expected_stage": "interested", "summary": "Invalid outcome", "outcome": "lost"},
    ]:
        assert (
            client.post(item + "/validate", headers=headers["admin"], json=payload).status_code
            == 409
        )
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={
                "expected_stage": "interested",
                "summary": "Éligibilité insuffisante pour ce marché.",
                "outcome": "withdrawn",
            },
        ).json()["stage"]
        == "withdrawn"
    )
    assert client.post(item + "/follow", headers=headers["alice"]).json()["stage"] == "withdrawn"


def test_company_cannot_wish_on_expired_opportunity(api_context, company, engine):
    client, headers, _ = api_context
    with session_factory(engine).begin() as session:
        session.get_one(Opportunity, "test-1").response_deadline_utc = datetime.now(
            UTC
        ) - timedelta(days=1)
    assert (
        client.post(
            f"/api/companies/{company.id}/applications/test-1/follow", headers=headers["alice"]
        ).status_code
        == 409
    )


def test_extended_onboarding_profile_experience_and_role_ownership(api_context, company, profile):
    client, headers, _ = api_context
    path = f"/api/companies/{company.id}/profile"
    data = profile.model_dump(mode="json")
    data["company"].update(
        {
            "address": "120 Test Street",
            "city": "Richmond",
            "uei": "TEST12345678",
            "cage_code": "1AB23",
            "service_categories": ["Janitorial services"],
        }
    )
    data["operations"] = {
        "employee_count": 18,
        "crew_count": 3,
        "concurrent_projects": 4,
        "availability": "limited",
        "available_from": "2027-01-10",
        "workers_comp_applicable": False,
        "workers_comp_explanation": "Owner-operated business without employees in this example",
        "licenses_applicable": True,
        "licenses": [
            {"name": "Business license", "issuer": "Test authority", "expires_on": "2027-12-31"}
        ],
        "certifications": [{"name": "ISO test", "issuer": "Test issuer"}],
        "rates": [{"service": "Office cleaning", "unit": "hour", "amount_usd": 42.5}],
    }
    assert client.put(path, headers=headers["alice"], json=data).status_code == 200
    saved = client.get(path, headers=headers["admin"]).json()["profile"]
    assert saved["operations"]["rates"][0]["amount_usd"] == 42.5
    assert saved["operations"]["licenses"][0]["expires_on"] == "2027-12-31"
    assert client.put(path, headers=headers["admin"], json=data).status_code == 403
    assert client.put(path, headers=headers["bob"], json=data).status_code == 404
    data["operations"]["workers_comp_explanation"] = ""
    assert client.put(path, headers=headers["alice"], json=data).status_code == 422
    statement = f"/api/companies/{company.id}/statement"
    assert (
        client.put(statement, headers=headers["admin"], json={"description": "tamper"}).status_code
        == 403
    )
    experience = {
        "government_experience": "Facility services for a municipal authority",
        "references": [
            {
                "customer": "Test City",
                "project": "Civic building cleaning",
                "year": 2025,
                "value_usd": 120000,
                "government": True,
                "contract_number": "TEST-01",
            }
        ],
    }
    assert (
        client.patch(
            statement + "/experience", headers=headers["alice"], json=experience
        ).status_code
        == 200
    )
    assert client.get(statement, headers=headers["admin"]).json()["statement"]["references"][0][
        "government"
    ]
    assert (
        client.patch(statement + "/experience", headers=headers["bob"], json=experience).status_code
        == 404
    )
    assert (
        client.patch(
            statement + "/experience", headers=headers["admin"], json=experience
        ).status_code
        == 403
    )


def test_onboarding_documents_private_review_expiry_and_checklist(api_context, company, profile):
    from pathlib import Path

    client, headers, second = api_context
    root = f"/api/companies/{company.id}/documents"
    raw = Path("tests/fixtures/company-statement.pdf").read_bytes()
    upload_headers = {**headers["alice"], "Content-Type": "application/pdf"}
    initial = client.get(root, headers=headers["alice"]).json()
    assert {row["kind"]: row["status"] for row in initial["requirements"]}[
        "workers_comp"
    ] == "to_confirm"
    response = client.post(root + "?kind=w9&title=W-9", headers=upload_headers, content=raw)
    assert response.status_code == 201
    doc = response.json()
    assert doc["status"] == "pending"
    path = root + f"/{doc['id']}"
    assert client.get(path + "/pdf", headers=headers["alice"]).content == raw
    assert client.get(path + "/pdf", headers=headers["admin"]).content == raw
    assert client.get(path + "/pdf", headers=headers["bob"]).status_code == 404
    assert (
        client.get(
            f"/api/companies/{second.id}/documents/{doc['id']}/pdf", headers=headers["bob"]
        ).status_code
        == 404
    )
    assert (
        client.post(
            path + "/review", headers=headers["alice"], json={"status": "accepted"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            path + "/review", headers=headers["admin"], json={"status": "rejected"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            path + "/review",
            headers=headers["admin"],
            json={"status": "rejected", "note": "Signature manquante"},
        ).status_code
        == 200
    )
    assert "Signature manquante" in client.get(root, headers=headers["alice"]).text
    assert (
        client.post(
            path + "/review",
            headers=headers["admin"],
            json={"status": "accepted", "note": "Vérifié"},
        ).json()["status"]
        == "accepted"
    )
    requirements = client.get(root, headers=headers["alice"]).json()["requirements"]
    assert next(row for row in requirements if row["kind"] == "w9")["status"] == "accepted"
    expired = client.post(
        root + "?kind=insurance&title=Expired&expires_on=2020-01-01",
        headers=upload_headers,
        content=raw,
    ).json()
    assert expired["status"] == "expired"
    assert (
        client.post(
            root + f"/{expired['id']}/review", headers=headers["admin"], json={"status": "accepted"}
        ).status_code
        == 400
    )
    assert client.delete(path, headers=headers["admin"]).status_code == 403
    assert client.delete(path, headers=headers["bob"]).status_code == 404
    assert client.delete(path, headers=headers["alice"]).status_code == 200
    assert client.get(path + "/pdf", headers=headers["alice"]).status_code == 404
    assert client.get(root).status_code == 401
    assert client.get(root, headers=headers["sub"]).status_code == 403
    assert (
        client.post(
            root + "?kind=w9&title=New",
            headers={**headers["admin"], "Content-Type": "application/pdf"},
            content=raw,
        ).status_code
        == 403
    )
    assert (
        client.post(
            root + "?kind=invalid&title=New", headers=upload_headers, content=raw
        ).status_code
        == 422
    )
    assert (
        client.post(
            root + "?kind=w9&title=Invalid", headers=upload_headers, content=b"bad PDF"
        ).status_code
        == 400
    )
    assert (
        client.post(
            root + "?kind=w9&title=WrongType", headers=headers["alice"], content=raw
        ).status_code
        == 400
    )


def complete_case_dossier(client, headers, company_id):
    from pathlib import Path

    raw = Path("tests/fixtures/company-statement.pdf").read_bytes()
    endpoint = f"/api/companies/{company_id}/applications/test-1/dossier"
    admin = headers["admin"]
    data = client.get(endpoint, headers=admin).json()
    response = client.post(
        endpoint + f"/documents?kind=prepared&title=Final&expected_revision={data['revision']}",
        headers={**admin, "Content-Type": "application/pdf"},
        content=raw,
    )
    assert response.status_code == 201
    prepared = response.json()
    source = client.post(
        f"/api/companies/{company_id}/documents?kind=w9&title=Signed",
        headers={**headers["alice"], "Content-Type": "application/pdf"},
        content=raw,
    ).json()
    assert (
        client.post(
            f"/api/companies/{company_id}/documents/{source['id']}/review",
            headers=admin,
            json={"status": "accepted"},
        ).status_code
        == 200
    )
    data = client.get(endpoint, headers=admin).json()
    assert (
        client.post(
            endpoint + "/import",
            headers=admin,
            json={"expected_revision": data["revision"], "source_document_id": source["id"]},
        ).status_code
        == 201
    )
    for item in client.get(endpoint, headers=admin).json()["checklist"]:
        data = client.get(endpoint, headers=admin).json()
        assert (
            client.patch(
                endpoint + f"/checklist/{item['id']}",
                headers=admin,
                json={
                    "expected_revision": data["revision"],
                    "status": "validated",
                    "note": "Checked against the solicitation, page 2.",
                },
            ).status_code
            == 200
        )
    data = client.get(endpoint, headers=admin).json()
    response = client.post(
        endpoint + "/approve",
        headers=admin,
        json={
            "expected_revision": data["revision"],
            "prepared_document_id": prepared["id"],
            "note": "Final review completed by administrator.",
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), prepared, source


def start_case_preparation(client, headers, company_id):
    item = f"/api/companies/{company_id}/applications/test-1"
    assert client.post(item + "/follow", headers=headers["alice"]).status_code == 200
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={"expected_stage": "interested", "summary": "Examination started"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/opportunities/test-1/screening?company_id={company_id}", headers=headers["admin"]
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/opportunities/test-1/decision?company_id={company_id}",
            headers=headers["admin"],
            json={"decision": "bid"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={"expected_stage": "examining", "summary": "Examination completed"},
        ).status_code
        == 200
    )
    return item


def test_dossier_final_control_and_immutable_versioned_archives(api_context, company, engine):
    import hashlib
    import json
    from io import BytesIO
    from pathlib import Path
    from zipfile import ZipFile

    from pypdf import PdfWriter

    client, headers, _ = api_context
    item = start_case_preparation(client, headers, company.id)
    endpoint = item + "/dossier"
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={"expected_stage": "preparing", "summary": "No file yet"},
        ).status_code
        == 409
    )
    initial = client.get(endpoint, headers=headers["admin"]).json()
    assert not initial["approved"] and len(initial["checklist"]) == 6 and initial["issues"]
    approval, prepared, source = complete_case_dossier(client, headers, company.id)
    assert client.get(endpoint, headers=headers["admin"]).json()["approved"]
    zipped = client.get(
        endpoint + f"/approvals/{approval['approval_id']}/package", headers=headers["admin"]
    )
    assert zipped.status_code == 200 and zipped.headers["cache-control"] == "no-store"
    with ZipFile(BytesIO(zipped.content)) as package:
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["approval"]["prepared_document_id"] == prepared["id"]
        original = package.read(f"prepared/document-{prepared['id']}-v1.pdf")
        assert hashlib.sha256(original).hexdigest() == prepared["sha256"]
        assert manifest["profile"]["company"]["legal_name"] == company.name
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=500, height=700)
    writer.write(stream)
    data = client.get(endpoint, headers=headers["admin"]).json()
    response = client.post(
        endpoint
        + f"/documents?kind=prepared&title=FinalV2&expected_revision={data['revision']}"
        + f"&previous_document_id={prepared['id']}",
        headers={**headers["admin"], "Content-Type": "application/pdf"},
        content=stream.getvalue(),
    )
    assert response.status_code == 201 and response.json()["version"] == 2
    assert not client.get(endpoint, headers=headers["admin"]).json()["approved"]
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={"expected_stage": "preparing", "summary": "Changed without final control"},
        ).status_code
        == 409
    )
    assert (
        client.get(endpoint + f"/documents/{prepared['id']}/pdf", headers=headers["admin"]).content
        == original
    )
    assert (
        client.get(
            endpoint + f"/approvals/{approval['approval_id']}/package", headers=headers["admin"]
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/companies/{company.id}/documents/{source['id']}", headers=headers["alice"]
        ).status_code
        == 200
    )
    copied = next(
        doc
        for doc in client.get(endpoint, headers=headers["admin"]).json()["documents"]
        if doc["kind"] == "contractor"
    )
    assert (
        client.get(endpoint + f"/documents/{copied['id']}/pdf", headers=headers["admin"]).content
        == Path("tests/fixtures/company-statement.pdf").read_bytes()
    )

    # A storage corruption must never produce an apparently valid review archive.
    from app.db.models import CaseDocument

    with session_factory(engine).begin() as session:
        session.get_one(CaseDocument, prepared["id"]).content = b"corrupted storage"
    assert (
        client.get(
            endpoint + f"/approvals/{approval['approval_id']}/package", headers=headers["admin"]
        ).status_code
        == 409
    )


def test_dossier_permissions_checklist_validation_and_stale_edits(api_context, company):
    from pathlib import Path

    client, headers, second = api_context
    item = start_case_preparation(client, headers, company.id)
    endpoint = item + "/dossier"
    for name in ["alice", "ops", "bob", "sub"]:
        assert client.get(endpoint, headers=headers[name]).status_code == 403
        assert (
            client.post(
                endpoint + "/approve",
                headers=headers[name],
                json={"expected_revision": 0, "prepared_document_id": 1, "note": "tamper"},
            ).status_code
            == 403
        )
    admin = headers["admin"]
    data = client.get(endpoint, headers=admin).json()
    check = data["checklist"][0]
    assert (
        client.patch(
            endpoint + f"/checklist/{check['id']}",
            headers=admin,
            json={"expected_revision": data["revision"], "status": "waived"},
        ).status_code
        == 409
    )
    assert (
        client.patch(
            endpoint + f"/checklist/{check['id']}",
            headers=admin,
            json={
                "expected_revision": data["revision"],
                "status": "waived",
                "note": "No separate document required; description reviewed.",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            endpoint + "/checklist",
            headers=admin,
            json={"expected_revision": data["revision"], "label": "Stale edit"},
        ).status_code
        == 409
    )
    revision = client.get(endpoint, headers=admin).json()["revision"]
    response = client.post(
        endpoint + "/checklist",
        headers=admin,
        json={"expected_revision": revision, "label": "Signed offer form required"},
    )
    assert response.status_code == 201
    assert not client.get(endpoint, headers=admin).json()["approved"]
    raw = Path("tests/fixtures/company-statement.pdf").read_bytes()
    revision = client.get(endpoint, headers=admin).json()["revision"]
    doc = client.post(
        endpoint + f"/documents?kind=solicitation&title=RFP&expected_revision={revision}",
        headers={**admin, "Content-Type": "application/pdf"},
        content=raw,
    ).json()
    other = f"/api/companies/{second.id}/applications/test-1"
    assert client.post(other + "/follow", headers=headers["bob"]).status_code == 200
    assert (
        client.get(other + f"/dossier/documents/{doc['id']}/pdf", headers=admin).status_code == 404
    )
    assert (
        client.patch(
            other + f"/dossier/checklist/{check['id']}",
            headers=admin,
            json={"expected_revision": 0, "status": "validated", "note": "tamper"},
        ).status_code
        == 404
    )
    revision = client.get(endpoint, headers=admin).json()["revision"]
    assert (
        client.post(
            endpoint + f"/documents/{doc['id']}/archive?expected_revision={revision}", headers=admin
        ).status_code
        == 200
    )
    assert next(
        d for d in client.get(endpoint, headers=admin).json()["documents"] if d["id"] == doc["id"]
    )["archived"]
    assert client.get(endpoint + f"/documents/{doc['id']}/pdf", headers=admin).content == raw
    revision = client.get(endpoint, headers=admin).json()["revision"]
    assert (
        client.post(
            endpoint + f"/documents?kind=prepared&title=Invalid&expected_revision={revision}",
            headers={**admin, "Content-Type": "application/pdf"},
            content=b"bad",
        ).status_code
        == 400
    )


def test_dossier_changes_invalidate_readiness_and_submitted_case_is_locked(
    api_context, company, engine
):
    client, headers, _ = api_context
    item = start_case_preparation(client, headers, company.id)
    endpoint = item + "/dossier"
    approval, prepared, _ = complete_case_dossier(client, headers, company.id)
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={"expected_stage": "preparing", "summary": "Dossier reviewed"},
        ).status_code
        == 200
    )
    with session_factory(engine).begin() as session:
        row = session.get_one(Company, company.id)
        row.statement = {"description": "Updated business statement"}
    assert not client.get(endpoint, headers=headers["admin"]).json()["approved"]
    send = {"expected_stage": "ready", "summary": "Sent externally", "evidence": "Receipt-123"}
    assert client.post(item + "/validate", headers=headers["admin"], json=send).status_code == 409
    data = client.get(endpoint, headers=headers["admin"]).json()
    assert (
        client.post(
            endpoint + "/approve",
            headers=headers["admin"],
            json={
                "expected_revision": data["revision"],
                "prepared_document_id": prepared["id"],
                "note": "Updated statement checked",
            },
        ).status_code
        == 200
    )
    assert client.post(item + "/validate", headers=headers["admin"], json=send).status_code == 200
    data = client.get(endpoint, headers=headers["admin"]).json()
    assert not data["editable"]
    assert (
        client.post(
            endpoint + "/checklist",
            headers=headers["admin"],
            json={"expected_revision": data["revision"], "label": "Too late"},
        ).status_code
        == 409
    )
    assert (
        client.get(
            endpoint + f"/approvals/{approval['approval_id']}/package", headers=headers["admin"]
        ).status_code
        == 200
    )


def test_dossier_import_requires_current_accepted_sources_and_supports_statement(
    api_context, company
):
    from pathlib import Path

    client, headers, second = api_context
    item = start_case_preparation(client, headers, company.id)
    endpoint = item + "/dossier"
    raw = Path("tests/fixtures/company-statement.pdf").read_bytes()
    source = client.post(
        f"/api/companies/{company.id}/documents?kind=insurance&title=Pending",
        headers={**headers["alice"], "Content-Type": "application/pdf"},
        content=raw,
    ).json()
    data = client.get(endpoint, headers=headers["admin"]).json()
    payload = {"expected_revision": data["revision"], "source_document_id": source["id"]}
    assert (
        client.post(endpoint + "/import", headers=headers["admin"], json=payload).status_code == 409
    )
    other = client.post(
        f"/api/companies/{second.id}/documents?kind=w9&title=OtherCompany",
        headers={**headers["bob"], "Content-Type": "application/pdf"},
        content=raw,
    ).json()
    payload["source_document_id"] = other["id"]
    assert (
        client.post(endpoint + "/import", headers=headers["admin"], json=payload).status_code == 404
    )
    assert (
        client.post(
            endpoint + f"/import-statement?expected_revision={data['revision']}",
            headers=headers["admin"],
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/companies/{company.id}/statement/pdf",
            headers={**headers["alice"], "Content-Type": "application/pdf"},
            content=raw,
        ).status_code
        == 200
    )
    copied = client.post(
        endpoint + f"/import-statement?expected_revision={data['revision']}",
        headers=headers["admin"],
    )
    assert copied.status_code == 201 and copied.json()["kind"] == "contractor"
    assert (
        client.delete(
            f"/api/companies/{company.id}/statement/pdf", headers=headers["alice"]
        ).status_code
        == 200
    )
    assert (
        client.get(
            endpoint + f"/documents/{copied.json()['id']}/pdf", headers=headers["admin"]
        ).content
        == raw
    )


def test_dossier_source_rejection_and_sam_changes_block_final_control(api_context, company, engine):
    client, headers, _ = api_context
    item = start_case_preparation(client, headers, company.id)
    endpoint = item + "/dossier"
    _, prepared, source = complete_case_dossier(client, headers, company.id)
    assert (
        client.post(
            f"/api/companies/{company.id}/documents/{source['id']}/review",
            headers=headers["admin"],
            json={"status": "rejected", "note": "Document withdrawn after further review"},
        ).status_code
        == 200
    )
    data = client.get(endpoint, headers=headers["admin"]).json()
    assert not data["approved"] and any("source" in issue for issue in data["issues"])
    assert (
        client.post(
            endpoint + "/approve",
            headers=headers["admin"],
            json={
                "expected_revision": data["revision"],
                "prepared_document_id": prepared["id"],
                "note": "Cannot bypass rejected source",
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/companies/{company.id}/documents/{source['id']}/review",
            headers=headers["admin"],
            json={"status": "accepted"},
        ).status_code
        == 200
    )
    with session_factory(engine).begin() as session:
        opp = session.get_one(Opportunity, "test-1")
        opp.set_aside_category, opp.set_aside_code = "eight_a", "8A"
    data = client.get(endpoint, headers=headers["admin"]).json()
    assert not data["approved"] and any(
        "critères obligatoires" in issue for issue in data["issues"]
    )
    assert (
        client.post(
            item + "/validate",
            headers=headers["admin"],
            json={"expected_stage": "preparing", "summary": "Cannot bypass new set-aside"},
        ).status_code
        == 409
    )


def outbox_token(client, headers, email):
    from urllib.parse import parse_qs, urlsplit

    messages = client.get("/api/mail/outbox", headers=headers["admin"]).json()["items"]
    mail = next(
        message for message in messages if message["recipient"] == email and message.get("body")
    )
    link = next(line for line in mail["body"].splitlines() if line.startswith("http"))
    return parse_qs(urlsplit(link).fragment)["account"][0], mail


def test_invitation_acceptance_single_use_members_and_session_revocation(api_context, company):
    client, headers, second = api_context
    invitation = {
        "email": "new-member@example.test",
        "company_id": company.id,
        "role": "ops_manager",
    }
    created = client.post("/api/invitations", headers=headers["admin"], json=invitation)
    assert created.status_code == 201 and created.json()["delivery_mode"] == "local"
    user_id = created.json()["user"]["id"]
    assert created.json()["user"]["invitation_pending"] and not created.json()["user"]["is_active"]
    token, _ = outbox_token(client, headers, invitation["email"])
    accepted = client.post(
        "/api/auth/accept-action", json={"token": token, "password": "new-account-password-123"}
    )
    assert accepted.status_code == 200
    assert (
        client.post(
            "/api/auth/accept-action", json={"token": token, "password": "new-account-password-123"}
        ).status_code
        == 409
    )
    login = client.post(
        "/api/auth/login",
        json={"email": invitation["email"], "password": "new-account-password-123"},
    )
    member_headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.get("/api/auth/me", headers=member_headers).json()["company_id"] == company.id
    assert client.get("/api/users", headers=member_headers).status_code == 403
    assert client.get("/api/mail/outbox", headers=member_headers).status_code == 403
    assert (
        client.get(f"/api/users?company_id={second.id}", headers=headers["admin"]).json()["total"]
        == 1
    )
    assert (
        client.patch(
            f"/api/users/{user_id}",
            headers=headers["alice"],
            json={"role": "ops_manager", "is_active": False},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/users/{user_id}",
            headers=headers["admin"],
            json={"role": "ops_manager", "is_active": False},
        ).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=member_headers).status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"email": invitation["email"], "password": "new-account-password-123"},
        ).status_code
        == 401
    )
    assert (
        client.patch(
            f"/api/users/{user_id}",
            headers=headers["admin"],
            json={"role": "business_developer", "is_active": True},
        ).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=member_headers).status_code == 401
    login = client.post(
        "/api/auth/login",
        json={"email": invitation["email"], "password": "new-account-password-123"},
    )
    assert login.status_code == 200
    fresh = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert (
        client.post(
            "/api/auth/change-password",
            headers=fresh,
            json={"current_password": "wrong", "password": "changed-account-password-123"},
        ).status_code
        == 409
    )
    assert client.get("/api/auth/me", headers=fresh).status_code == 200
    assert (
        client.post(
            "/api/auth/change-password",
            headers=fresh,
            json={
                "current_password": "new-account-password-123",
                "password": "changed-account-password-123",
            },
        ).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=fresh).status_code == 401
    login = client.post(
        "/api/auth/login",
        json={"email": invitation["email"], "password": "changed-account-password-123"},
    )
    fresh = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.post("/api/auth/logout", headers=fresh).status_code == 200
    assert client.get("/api/auth/me", headers=fresh).status_code == 401


def test_password_reset_generic_response_expiry_resend_and_throttling(api_context, company, engine):
    from app.db.models import AccountAction

    client, headers, _ = api_context
    known = client.post("/api/auth/forgot-password", json={"email": "alice@example.test"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "missing@example.test"})
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    token, mail = outbox_token(client, headers, "alice@example.test")
    with session_factory(engine)() as session:
        action = session.get_one(
            AccountAction,
            session.query(AccountAction.id)
            .filter(
                AccountAction.user_id
                == int(client.get("/api/auth/me", headers=headers["alice"]).json()["id"])
            )
            .scalar(),
        )
        assert action.token_hash != token and token not in action.token_hash
    assert (
        client.post(
            "/api/auth/accept-action",
            json={"token": token, "password": "reset-account-password-123"},
        ).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=headers["alice"]).status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "alice@example.test", "password": "reset-account-password-123"},
        ).status_code
        == 200
    )
    for _ in range(4):
        assert (
            client.post(
                "/api/auth/forgot-password", json={"email": "alice@example.test"}
            ).status_code
            == 200
        )
    assert (
        client.post("/api/auth/forgot-password", json={"email": "alice@example.test"}).status_code
        == 429
    )
    token, _ = outbox_token(client, headers, "alice@example.test")
    with session_factory(engine).begin() as session:
        import hashlib

        action = session.scalar(
            select(AccountAction).where(
                AccountAction.token_hash == hashlib.sha256(token.encode()).hexdigest()
            )
        )
        action.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert (
        client.post(
            "/api/auth/accept-action",
            json={"token": token, "password": "reset-account-password-123"},
        ).status_code
        == 409
    )
    created = client.post(
        "/api/invitations",
        headers=headers["admin"],
        json={"email": "pending@example.test", "company_id": company.id, "role": "ops_manager"},
    ).json()
    old, _ = outbox_token(client, headers, "pending@example.test")
    assert (
        client.post(
            f"/api/users/{created['user']['id']}/resend-invitation", headers=headers["admin"]
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/accept-action", json={"token": old, "password": "new-invited-password-123"}
        ).status_code
        == 409
    )
    latest, _ = outbox_token(client, headers, "pending@example.test")
    assert (
        client.patch(
            f"/api/users/{created['user']['id']}",
            headers=headers["admin"],
            json={"role": "ops_manager", "is_active": False},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/accept-action",
            json={"token": latest, "password": "new-invited-password-123"},
        ).status_code
        == 409
    )


def test_smtp_delivery_failure_retry_and_encrypted_outbox(
    api_context, settings, engine, monkeypatch
):
    from app.db.models import EmailOutbox
    from app.services.mailer import deliver

    client, headers, _ = api_context
    settings.mail_mode, settings.smtp_host = "smtp", "smtp.example.test"
    sent = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, **kwargs):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr("app.services.mailer.smtplib.SMTP", FakeSMTP)
    assert (
        client.post("/api/auth/forgot-password", json={"email": "alice@example.test"}).status_code
        == 200
    )
    messages = client.get("/api/mail/outbox", headers=headers["admin"]).json()["items"]
    assert messages[0]["status"] == "sent" and "body" not in messages[0]
    assert len(sent) == 1 and "#account=" in sent[0].get_content()
    with session_factory(engine)() as session:
        encrypted = session.get_one(EmailOutbox, messages[0]["id"]).encrypted_body
        assert b"#account=" not in encrypted

    class BrokenSMTP(FakeSMTP):
        def send_message(self, message):
            raise RuntimeError("provider response MUST NOT leak")

    monkeypatch.setattr("app.services.mailer.smtplib.SMTP", BrokenSMTP)
    assert (
        client.post("/api/auth/forgot-password", json={"email": "bob@example.test"}).status_code
        == 200
    )
    message = client.get("/api/mail/outbox", headers=headers["admin"]).json()["items"][0]
    assert message["status"] == "failed" and "MUST NOT" not in str(message)
    monkeypatch.setattr("app.services.mailer.smtplib.SMTP", FakeSMTP)
    assert (
        client.post(f"/api/mail/outbox/{message['id']}/retry", headers=headers["admin"]).status_code
        == 200
    )
    assert (
        client.get("/api/mail/outbox", headers=headers["admin"]).json()["items"][0]["status"]
        == "sent"
    )
    deliver(engine, settings, message["id"])
    assert len(sent) == 2


def test_reports_statistics_exports_and_public_history_are_scoped(api_context, company, engine):
    from app.db.models import ApplicationMilestone, CompanyCase

    client, headers, second = api_context
    now = datetime.now(UTC)
    with session_factory(engine).begin() as session:
        for company_id, stage, summary in [
            (company.id, "won", "Public result A"),
            (second.id, "lost", "PRIVATE company B"),
        ]:
            row = CompanyCase(
                company_id=company_id,
                notice_id="test-1",
                application_stage=stage,
                bid_decision="bid",
                application_updated_at=now,
                application_notes="PRIVATE internal notes",
            )
            session.add(row)
            session.flush()
            admin_id = int(client.get("/api/auth/me", headers=headers["admin"]).json()["id"])
            session.add(
                ApplicationMilestone(
                    case_id=row.id,
                    from_stage="submitted",
                    to_stage=stage,
                    summary=summary,
                    evidence="PRIVATE receipt",
                    validated_by=admin_id,
                )
            )
        session.get_one(Opportunity, "test-1").title = "=DANGEROUS spreadsheet formula"
    admin = client.get("/api/reports/statistics", headers=headers["admin"]).json()
    assert admin["total"] == 2 and admin["success_rate"] == 50.0
    private = client.get("/api/reports/statistics", headers=headers["alice"]).json()
    assert private["total"] == 1 and private["won"] == 1 and private["success_rate"] == 100.0
    assert len(private["companies"]) == 1
    assert (
        client.get(
            f"/api/reports/statistics?company_id={second.id}", headers=headers["alice"]
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/reports/statistics?start=2027-01-01&end=2020-01-01", headers=headers["admin"]
        ).status_code
        == 409
    )
    assert (
        client.get("/api/reports/statistics?end=2020-01-01", headers=headers["admin"]).json()[
            "total"
        ]
        == 0
    )
    history = client.get("/api/reports/history", headers=headers["alice"])
    assert "Public result A" in history.text and "PRIVATE" not in history.text
    exported = client.get("/api/reports/export/applications", headers=headers["alice"])
    assert exported.status_code == 200 and exported.content.startswith(b"\xef\xbb\xbf")
    assert "'=DANGEROUS" in exported.text and second.name not in exported.text
    assert "PRIVATE" not in client.get("/api/reports/export/history", headers=headers["alice"]).text
    assert client.get("/api/reports/history?page=2", headers=headers["alice"]).json()["items"] == []
    assert client.get("/api/reports/history", headers=headers["sub"]).status_code == 403
    assert client.get("/api/reports/statistics").status_code == 401


def test_admin_report_history_scopes_and_exports_and_account_guards(api_context, company, engine):
    from app.services.audit import audit

    client, headers, second = api_context
    with session_factory(engine).begin() as session:
        audit(
            session,
            "test.company_A",
            "company",
            str(company.id),
            actor_id="reviewer",
            after={"note": "A-only"},
        )
        audit(
            session,
            "test.company_B",
            "company",
            str(second.id),
            actor_id="reviewer",
            after={"note": "B-only"},
        )
    history = client.get(f"/api/reports/history?company_id={company.id}", headers=headers["admin"])
    assert "A-only" in history.text and "B-only" not in history.text
    exported = client.get(
        f"/api/reports/export/history?company_id={company.id}", headers=headers["admin"]
    )
    assert "test.company_A" in exported.text and "test.company_B" not in exported.text
    future = client.get("/api/reports/history?start=2099-01-01", headers=headers["admin"])
    assert future.json()["items"] == []
    admin_id = client.get("/api/auth/me", headers=headers["admin"]).json()["id"]
    assert (
        client.patch(
            f"/api/users/{admin_id}",
            headers=headers["admin"],
            json={"is_active": False, "role": "ops_manager"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/invitations",
            headers=headers["admin"],
            json={"email": "missing-space@example.test", "company_id": 99999},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/invitations",
            headers=headers["alice"],
            json={"email": "forbidden@example.test", "company_id": company.id},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/invitations",
            headers=headers["admin"],
            json={"email": "alice@example.test", "company_id": company.id},
        ).status_code
        == 409
    )
    assert (
        client.get(f"/api/mail/outbox?company_id={second.id}", headers=headers["alice"]).status_code
        == 403
    )
