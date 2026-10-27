import copy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.db.base import session_factory
from app.db.models import AgentReview, Company, CompanyCase, ScreeningResult, User
from app.main import create_app
from app.services import agent_reviews as reviews
from app.services.auth import DUMMY_HASH, issue_token
from app.services.hermes_runtime import work_once
from app.services.review_context import build_snapshot, context_hash, context_manifest
from scripts.smoke_test_hermes import REPORT


@pytest.fixture
def review_context(engine, settings, company, seeded_opp):
    settings.jwt_secret = SecretStr("hermes-integration-test-signing-key-at-least-32-characters")
    with session_factory(engine).begin() as session:
        second = Company(name="OTHER PRIVATE COMPANY", profile=company.profile)
        session.add(second)
        session.flush()
        users = {}
        for name, role, cid in [
            ("admin", "admin", None),
            ("client", "business_developer", company.id),
            ("other", "ops_manager", second.id),
        ]:
            user = User(
                email=name + "@agent.test", password_hash=DUMMY_HASH, role=role, company_id=cid
            )
            session.add(user)
            session.flush()
            users[name] = user
        session.add(
            CompanyCase(
                company_id=company.id,
                notice_id=seeded_opp.notice_id,
                application_stage="examining",
                application_origin="admin",
            )
        )
        session.add(
            CompanyCase(
                company_id=second.id, notice_id=seeded_opp.notice_id, application_stage="examining"
            )
        )
    app = create_app(settings)
    with TestClient(app) as client:
        # Enable after startup; these tests drive the worker without a real model.
        settings.hermes_enabled = True
        settings.hermes_python = Path(sys.executable)
        settings.hermes_api_key = SecretStr("hermes-test-key-NEVER-LOG")
        app.state.hermes_worker_thread = SimpleNamespace(is_alive=lambda: True)
        headers = {
            name: {"Authorization": "Bearer " + issue_token(user, settings)}
            for name, user in users.items()
        }
        endpoint = f"/api/companies/{company.id}/applications/test-1/agent-examinations"
        yield client, headers, endpoint, second


def output(snapshot):
    report = copy.deepcopy(REPORT)
    report["requirements"][0]["citations"][0]["quote"] = "Licensed work is required."
    return {
        "report": report,
        "tool_calls": [
            {"name": "read_source", "source_id": source["id"]} for source in snapshot["sources"]
        ],
    }


def test_hermes_permissions_configuration_and_case_isolation(review_context, settings):
    client, headers, endpoint, second = review_context
    for name in ["client", "other"]:
        for method, suffix, body in [
            ("get", "", None),
            ("post", "", None),
            ("post", "/1/cancel", None),
            ("post", "/1/decision", {"decision": "accepted", "note": "Attempt"}),
            ("get", "/1/report", None),
        ]:
            response = client.request(
                method, endpoint + suffix, headers=headers[name], **({"json": body} if body else {})
            )
            assert response.status_code == 403
    settings.hermes_enabled = False
    assert client.get(endpoint, headers=headers["admin"]).json()["enabled"] is False
    assert client.post(endpoint, headers=headers["admin"]).status_code == 503
    settings.hermes_enabled = True
    row = client.post(endpoint, headers=headers["admin"]).json()
    other = endpoint.replace("/companies/1/", f"/companies/{second.id}/")
    assert client.get(other + f"/{row['id']}/report", headers=headers["admin"]).status_code == 404
    assert client.post(other + f"/{row['id']}/cancel", headers=headers["admin"]).status_code == 404


def test_missing_worker_is_explained_and_cannot_create_a_stuck_job(review_context, engine):
    client, headers, endpoint, _ = review_context
    client.app.state.hermes_worker_thread = None
    state = client.get(endpoint, headers=headers["admin"]).json()
    assert state["configuration"]["code"] == "ready" and state["worker_running"] is False
    response = client.post(endpoint, headers=headers["admin"])
    assert response.status_code == 503 and "Redémarrez" in response.json()["error"]["message"]
    with session_factory(engine)() as session:
        assert session.scalar(select(AgentReview)) is None


def test_durable_job_human_review_and_bid_remain_separate(
    review_context, engine, settings, company
):
    client, headers, endpoint, _ = review_context
    first = client.post(endpoint, headers=headers["admin"])
    assert first.status_code == 202
    run_id = first.json()["id"]
    assert client.post(endpoint, headers=headers["admin"]).json()["id"] == run_id
    assert (
        client.post(
            endpoint + f"/{run_id}/decision",
            headers=headers["admin"],
            json={"decision": "accepted", "note": "Too early"},
        ).status_code
        == 409
    )
    assert work_once(
        engine, settings, Event(), lambda s, snapshot, m, cancelled: (output(snapshot), None)
    )
    assert not work_once(engine, settings, Event(), lambda *args: (None, "runtime_error"))
    row = client.get(endpoint, headers=headers["admin"]).json()["items"][0]
    assert row["status"] == "succeeded" and row["review_decision"] is None
    assert not row["stale"] and row["report"]["recommendation"] == "review"
    assert (
        "OTHER PRIVATE COMPANY"
        not in client.get(endpoint + f"/{run_id}/report", headers=headers["admin"]).text
    )
    with session_factory(engine)() as session:
        case = session.scalar(select(CompanyCase).where(CompanyCase.company_id == company.id))
        assert (
            case.application_stage == "examining"
            and case.bid_decision is None
            and case.status == "new"
        )
    response = client.post(
        endpoint + f"/{run_id}/decision",
        headers=headers["admin"],
        json={"decision": "accepted", "note": "Exigences et sources contrôlées."},
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(
            endpoint + f"/{run_id}/decision",
            headers=headers["admin"],
            json={"decision": "accepted", "note": "Repeat"},
        ).status_code
        == 409
    )
    with session_factory(engine)() as session:
        case = session.scalar(select(CompanyCase).where(CompanyCase.company_id == company.id))
        assert (
            case.application_stage == "examining"
            and case.bid_decision is None
            and case.status == "screening"
        )
        assert (
            session.scalar(select(ScreeningResult).where(ScreeningResult.case_id == case.id))
            is not None
        )
    assert (
        client.post(
            f"/api/opportunities/test-1/decision?company_id={company.id}",
            headers=headers["admin"],
            json={"decision": "bid"},
        ).status_code
        == 200
    )


def test_stale_report_cannot_be_approved_and_snapshot_is_immutable(
    review_context, engine, settings, company
):
    client, headers, endpoint, _ = review_context
    run_id = client.post(endpoint, headers=headers["admin"]).json()["id"]
    assert work_once(engine, settings, Event(), lambda s, snapshot, m, c: (output(snapshot), None))
    with session_factory(engine).begin() as session:
        current = session.get_one(Company, company.id)
        profile = copy.deepcopy(current.profile)
        profile["company"]["legal_name"] = "Company changed after snapshot"
        current.profile = profile
    public = client.get(endpoint, headers=headers["admin"]).json()["items"][0]
    assert public["stale"]
    assert (
        client.post(
            endpoint + f"/{run_id}/decision",
            headers=headers["admin"],
            json={"decision": "accepted", "note": "Stale"},
        ).status_code
        == 409
    )
    with session_factory(engine)() as session:
        snapshot = session.get_one(AgentReview, run_id).snapshot
        assert "Company changed after snapshot" not in str(snapshot)
    assert client.post(endpoint, headers=headers["admin"]).json()["id"] != run_id


@pytest.mark.parametrize("failure", ["quote", "source", "trace", "tool", "missing"])
def test_unverifiable_results_fail_and_allow_retry(review_context, engine, settings, failure):
    client, headers, endpoint, _ = review_context
    run_id = client.post(endpoint, headers=headers["admin"]).json()["id"]

    def execute(s, snapshot, m, c):
        result = output(snapshot)
        if failure == "quote":
            result["report"]["requirements"][0]["citations"][0]["quote"] = "Invented qualification"
        if failure == "source":
            result["report"]["requirements"][0]["citations"][0]["source_id"] = "OTHER COMPANY"
        if failure == "trace":
            result["tool_calls"] = []
        if failure == "tool":
            result["tool_calls"] = [{"name": "terminal"}]
        if failure == "missing":
            del result["report"]["summary"]
        return result, None

    work_once(engine, settings, Event(), execute)
    row = client.get(endpoint, headers=headers["admin"]).json()["items"][0]
    assert row["status"] == "failed" and row["report"] is None
    assert "hermes-test-key" not in str(row)
    assert client.post(endpoint, headers=headers["admin"]).json()["id"] != run_id


def test_cancel_recovery_budgets_and_late_results(review_context, engine, settings):
    client, headers, endpoint, _ = review_context
    run_id = client.post(endpoint, headers=headers["admin"]).json()["id"]
    assert reviews.claim(engine, settings) == run_id
    assert reviews.claim(engine, settings) is None
    assert client.post(endpoint + f"/{run_id}/cancel", headers=headers["admin"]).status_code == 200
    with session_factory(engine)() as session:
        snapshot = session.get_one(AgentReview, run_id).snapshot
    reviews.finish(engine, run_id, output(snapshot), None)
    assert (
        client.get(endpoint, headers=headers["admin"]).json()["items"][0]["status"] == "cancelled"
    )
    next_id = client.post(endpoint, headers=headers["admin"]).json()["id"]
    assert reviews.claim(engine, settings) == next_id
    with session_factory(engine).begin() as session:
        session.get_one(AgentReview, next_id).lease_until = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get(endpoint, headers=headers["admin"]).json()["items"][0]["status"] == "failed"
    third = client.post(endpoint, headers=headers["admin"]).json()["id"]
    assert third != next_id
    assert client.post(endpoint + f"/{third}/cancel", headers=headers["admin"]).status_code == 200
    settings.hermes_daily_run_limit = 1
    assert client.post(endpoint, headers=headers["admin"]).status_code == 409


def test_context_reads_only_selected_sources_and_pdf_limits(review_context, engine, company):
    import hashlib

    from app.db.models import CaseDocument

    client, headers, endpoint, _ = review_context
    raw = Path("tests/fixtures/company-statement.pdf").read_bytes()
    with session_factory(engine).begin() as session:
        case = session.scalar(select(CompanyCase).where(CompanyCase.company_id == company.id))
        admin = session.scalar(select(User).where(User.role == "admin"))
        session.add(
            CaseDocument(
                case_id=case.id,
                document_key="source",
                version=1,
                kind="solicitation",
                title="PDF consultation",
                content=raw,
                sha256=hashlib.sha256(raw).hexdigest(),
                uploaded_by=admin.id,
            )
        )
    with session_factory(engine)() as session:
        case = session.scalar(select(CompanyCase).where(CompanyCase.company_id == company.id))
        manifest = context_manifest(session, case)
        snapshot = build_snapshot(session, case, manifest)
        # This fixture contains a blank page: do not pretend it was read or OCR'd.
        assert not any(s["id"].startswith("document:") for s in snapshot["sources"])
        assert any("OCR non effectué" in warning for warning in snapshot["warnings"])
        assert "OTHER PRIVATE COMPANY" not in str(snapshot)
        assert len(context_hash(manifest)) == 64


@pytest.mark.parametrize("invalid", ["pending_request", "closed_case", "expired_notice"])
def test_examination_requires_admin_intake_and_live_notice(
    review_context, engine, company, invalid
):
    from app.db.models import Opportunity

    client, headers, endpoint, _ = review_context
    with session_factory(engine).begin() as session:
        case = session.scalar(select(CompanyCase).where(CompanyCase.company_id == company.id))
        if invalid == "pending_request":
            case.application_stage = "interested"
        elif invalid == "closed_case":
            case.application_stage = "rejected"
        else:
            session.get_one(Opportunity, "test-1").response_deadline_utc = datetime.now(
                UTC
            ) - timedelta(days=1)
    assert client.post(endpoint, headers=headers["admin"]).status_code == 409


def test_human_acceptance_cannot_override_objective_eligibility(
    review_context, engine, settings, company
):
    from app.db.models import Opportunity

    client, headers, endpoint, _ = review_context
    with session_factory(engine).begin() as session:
        opp = session.get_one(Opportunity, "test-1")
        opp.set_aside_category, opp.set_aside_code = "eight_a", "8A"
    run_id = client.post(endpoint, headers=headers["admin"]).json()["id"]
    work_once(engine, settings, Event(), lambda s, snapshot, m, c: (output(snapshot), None))
    assert (
        client.post(
            endpoint + f"/{run_id}/decision",
            headers=headers["admin"],
            json={
                "decision": "accepted",
                "note": "Interprétation relue, éligibilité restant à vérifier.",
            },
        ).status_code
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


def test_refusing_a_report_does_not_refuse_the_candidature(
    review_context, engine, settings, company
):
    client, headers, endpoint, _ = review_context
    run_id = client.post(endpoint, headers=headers["admin"]).json()["id"]
    work_once(engine, settings, Event(), lambda s, snapshot, m, c: (output(snapshot), None))
    assert (
        client.post(
            endpoint + f"/{run_id}/decision",
            headers=headers["admin"],
            json={"decision": "rejected", "note": "Analyse à reprendre."},
        ).status_code
        == 200
    )
    with session_factory(engine)() as session:
        case = session.scalar(select(CompanyCase).where(CompanyCase.company_id == company.id))
        assert case.application_stage == "examining" and case.bid_decision is None
    assert client.post(endpoint, headers=headers["admin"]).json()["id"] != run_id


def test_consultation_pdf_text_is_extracted_with_page_references_and_page_budget(
    review_context, engine, company, monkeypatch
):
    import hashlib
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    from app.db.models import CaseDocument
    from app.services import review_context as contexts

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (Insurance evidence required.) Tj ET")
    page[NameObject("/Contents")] = stream
    buffer = io.BytesIO()
    writer.write(buffer)
    raw = buffer.getvalue()
    with session_factory(engine).begin() as session:
        case = session.scalar(select(CompanyCase).where(CompanyCase.company_id == company.id))
        admin = session.scalar(select(User).where(User.role == "admin"))
        session.add(
            CaseDocument(
                case_id=case.id,
                document_key="textual-source",
                version=1,
                kind="solicitation",
                title="Textual consultation",
                content=raw,
                sha256=hashlib.sha256(raw).hexdigest(),
                uploaded_by=admin.id,
            )
        )
    with session_factory(engine)() as session:
        case = session.scalar(select(CompanyCase).where(CompanyCase.company_id == company.id))
        manifest = contexts.context_manifest(session, case)
        snapshot = contexts.build_snapshot(session, case, manifest)
        pages = [source for source in snapshot["sources"] if source["id"].startswith("document:")]
        assert len(pages) == 1 and pages[0]["id"].endswith(":page:1")
        assert "Insurance evidence required." in pages[0]["text"]
        monkeypatch.setattr(contexts, "MAX_PAGES", 0)
        limited = contexts.build_snapshot(session, case, manifest)
        assert not any(source["id"].startswith("document:") for source in limited["sources"])
        assert any("Limite de lecture" in warning for warning in limited["warnings"])
