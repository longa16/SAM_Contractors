import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO, cast
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.opportunity_analyst import triage
from app.db.models import (
    CaseChecklistItem,
    CaseDocument,
    CaseDossier,
    Company,
    CompanyCase,
    CompanyDocument,
    DossierApproval,
    Opportunity,
)
from app.errors import Conflict, NotFound
from app.schemas.company import CompanyProfile
from app.services.audit import audit

DEFAULT_CHECKS = [
    ("consultation", "Consultation et exigences de l’acheteur examinées"),
    ("eligibility", "Éligibilité, qualifications et décision Bid vérifiées"),
    ("contractor_documents", "Pièces de l’entreprise requises pour ce marché réunies"),
    ("technical", "Offre technique et réponses aux exigences vérifiées"),
    ("pricing", "Offre financière, montants et conditions vérifiés"),
    ("assembly", "Dossier final, signatures, formats et modalités de dépôt vérifiés"),
]


def ensure_dossier(session: Session, case: CompanyCase) -> CaseDossier:
    dossier = session.get(CaseDossier, case.id)
    if dossier is not None:
        return dossier
    try:
        with session.begin_nested():
            dossier = CaseDossier(case_id=case.id, revision=0)
            session.add(dossier)
            session.flush()
            for code, label in DEFAULT_CHECKS:
                session.add(CaseChecklistItem(case_id=case.id, code=code, label=label))
            session.flush()
            audit(
                session,
                "dossier.created",
                "company_case",
                str(case.id),
                meta={"company_id": case.company_id},
            )
    except IntegrityError:
        dossier = session.get_one(CaseDossier, case.id)
    return dossier


def lock_edit(session: Session, case: CompanyCase, expected_revision: int) -> CaseDossier:
    # Shared order with stage validation: lock the case before inspecting its dossier.
    stage = case.application_stage
    changed = session.execute(
        update(CompanyCase)
        .where(CompanyCase.id == case.id, CompanyCase.application_stage == stage)
        .values(updated_at=datetime.now(UTC))
    )
    if changed.rowcount != 1:  # type: ignore[attr-defined]
        raise Conflict("L’étape a changé ; actualisez le dossier.")
    if stage in {"submitted", "won", "lost", "withdrawn", "rejected"}:
        raise Conflict(
            "Ce dossier est clôturé pour les modifications. Son historique reste disponible."
        )
    dossier = ensure_dossier(session, case)
    changed = session.execute(
        update(CaseDossier)
        .where(CaseDossier.case_id == case.id, CaseDossier.revision == expected_revision)
        .values(revision=expected_revision + 1)
    )
    if changed.rowcount != 1:  # type: ignore[attr-defined]
        raise Conflict("Le dossier a changé ; actualisez avant de poursuivre.")
    session.refresh(dossier)
    return dossier


def all_documents(session: Session, case_id: int) -> list[CaseDocument]:
    return list(
        session.scalars(
            select(CaseDocument).where(CaseDocument.case_id == case_id).order_by(CaseDocument.id)
        ).all()
    )


def latest_documents(rows: list[CaseDocument]) -> list[CaseDocument]:
    latest: dict[str, CaseDocument] = {}
    for row in rows:
        if not row.archived:
            latest[row.document_key] = row
    return list(latest.values())


def document_data(doc: CaseDocument) -> dict[str, Any]:
    return {
        "id": doc.id,
        "document_key": doc.document_key,
        "version": doc.version,
        "kind": doc.kind,
        "title": doc.title,
        "sha256": doc.sha256,
        "source_document_id": doc.source_document_id,
        "expires_on": doc.expires_on.isoformat() if doc.expires_on else None,
        "archived": doc.archived,
        "uploaded_by": doc.uploaded_by,
        "created_at": doc.created_at.isoformat(),
    }


def checklist_data(item: CaseChecklistItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "code": item.code,
        "label": item.label,
        "required": item.required,
        "status": item.status,
        "note": item.note,
        "checked_by": item.checked_by,
        "checked_at": item.checked_at.isoformat() if item.checked_at else None,
    }


def get_document(session: Session, case: CompanyCase, document_id: int) -> CaseDocument:
    doc = session.get(CaseDocument, document_id)
    if doc is None or doc.case_id != case.id:
        raise NotFound("Document de candidature introuvable.")
    return doc


def add_document(
    session: Session,
    case: CompanyCase,
    *,
    expected_revision: int,
    kind: str,
    title: str,
    content: bytes,
    actor_id: int,
    previous_document_id: int | None = None,
    source: CompanyDocument | None = None,
) -> CaseDocument:
    lock_edit(session, case, expected_revision)
    previous = get_document(session, case, previous_document_id) if previous_document_id else None
    if previous:
        rows = latest_documents(all_documents(session, case.id))
        if previous.id not in {doc.id for doc in rows} or previous.kind != kind:
            raise Conflict("Choisissez la dernière version active du même type de document.")
    doc = CaseDocument(
        case_id=case.id,
        document_key=previous.document_key if previous else str(uuid4()),
        version=previous.version + 1 if previous else 1,
        kind=kind,
        title=title,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        source_document_id=source.id if source else None,
        expires_on=source.expires_on if source else None,
        uploaded_by=actor_id,
    )
    session.add(doc)
    session.flush()
    audit(
        session,
        "dossier.document_added",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor_id),
        meta={"company_id": case.company_id, "document_id": doc.id, "version": doc.version},
    )
    return doc


def build_manifest(session: Session, case: CompanyCase) -> dict[str, Any]:
    dossier = ensure_dossier(session, case)
    company = session.get_one(Company, case.company_id)
    opp = session.get_one(Opportunity, case.notice_id)
    checklist = session.scalars(
        select(CaseChecklistItem)
        .where(CaseChecklistItem.case_id == case.id)
        .order_by(CaseChecklistItem.id)
    ).all()
    # Include live eligibility data so a later profile/SAM change invalidates final approval.
    current_sources = session.scalars(
        select(CompanyDocument)
        .where(CompanyDocument.company_id == company.id)
        .order_by(CompanyDocument.id)
    ).all()
    return {
        "case_id": case.id,
        "company_id": company.id,
        "notice_id": case.notice_id,
        "revision": dossier.revision,
        "company_name": company.name,
        "profile": company.profile,
        "statement": company.statement,
        "statement_pdf_sha256": hashlib.sha256(company.statement_pdf).hexdigest()
        if company.statement_pdf
        else None,
        "company_documents": [
            {
                "id": d.id,
                "review_status": d.review_status,
                "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None,
                "expires_on": str(d.expires_on) if d.expires_on else None,
            }
            for d in current_sources
        ],
        "opportunity": {
            "title": opp.title,
            "active": opp.active,
            "deadline": opp.response_deadline_utc.isoformat()
            if opp.response_deadline_utc
            else None,
            "description": opp.description_text,
            "raw": {k: v for k, v in opp.raw_payload.items() if k != "_synced_at"},
        },
        "bid_decision": case.bid_decision,
        "documents": [
            document_data(doc) for doc in latest_documents(all_documents(session, case.id))
        ],
        "checklist": [checklist_data(item) for item in checklist],
    }


def fingerprint(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def preflight(
    session: Session, case: CompanyCase, manifest: dict[str, Any], selected_id: int | None = None
) -> list[str]:
    issues = []
    opp = session.get_one(Opportunity, case.notice_id)
    if not opp.active:
        issues.append("L’offre est inactive.")
    if opp.response_deadline_utc and opp.response_deadline_utc < datetime.now(UTC):
        issues.append("La date limite de réponse est dépassée.")
    if case.bid_decision != "bid" or case.analysis_status == "stale_profile":
        issues.append("L’éligibilité et la décision Bid doivent être à jour.")
    if not manifest["profile"]:
        issues.append("Le profil de l’entreprise est manquant.")
    if (
        manifest["profile"]
        and triage(
            opp, CompanyProfile.model_validate(manifest["profile"]), datetime.now(UTC)
        ).hard_blocked
    ):
        issues.append("L’offre ne respecte plus les critères obligatoires de l’entreprise.")
    docs = manifest["documents"]
    if not any(d["kind"] == "solicitation" for d in docs) and not opp.description_text:
        issues.append(
            "Ajoutez les documents de consultation ou récupérez la description de l’avis."
        )
    prepared = [d for d in docs if d["kind"] == "prepared"]
    if not prepared or (selected_id is not None and selected_id not in {d["id"] for d in prepared}):
        issues.append("Choisissez une dernière version active du dossier préparé en PDF.")
    sources = {d["id"]: d for d in manifest["company_documents"]}
    for doc in docs:
        source = sources.get(doc["source_document_id"])
        if source and source["review_status"] != "accepted":
            issues.append("Le justificatif source doit être contrôlé à nouveau : " + doc["title"])
        if doc["expires_on"] and date.fromisoformat(doc["expires_on"]) < date.today():
            issues.append("Justificatif expiré : " + doc["title"])
    for item in manifest["checklist"]:
        if item["required"] and item["status"] not in {"validated", "waived"}:
            issues.append("Contrôle à terminer : " + item["label"])
        if (
            item["code"] == "contractor_documents"
            and item["status"] == "validated"
            and not any(d["kind"] == "contractor" for d in docs)
        ):
            issues.append(
                "Importez les pièces contractor requises ou justifiez "
                "leur non-applicabilité dans la checklist."
            )
    return issues


def dossier_state(session: Session, case: CompanyCase) -> dict[str, Any]:
    manifest = build_manifest(session, case)
    approvals = list(
        session.scalars(
            select(DossierApproval)
            .where(DossierApproval.case_id == case.id)
            .order_by(DossierApproval.id.desc())
        ).all()
    )
    latest = approvals[0] if approvals else None
    issues = preflight(session, case, manifest, latest.prepared_document_id if latest else None)
    approved = bool(
        latest
        and latest.revision == manifest["revision"]
        and latest.fingerprint == fingerprint(manifest)
        and not issues
    )
    return {
        "case_id": case.id,
        "title": manifest["opportunity"]["title"],
        "revision": manifest["revision"],
        "documents": [document_data(doc) for doc in all_documents(session, case.id)],
        "checklist": manifest["checklist"],
        "issues": issues,
        "approved": approved,
        "approvals": [
            {
                "id": a.id,
                "revision": a.revision,
                "prepared_document_id": a.prepared_document_id,
                "note": a.note,
                "approved_by": a.approved_by,
                "created_at": a.created_at.isoformat(),
            }
            for a in approvals
        ],
    }


def approve(
    session: Session,
    case: CompanyCase,
    *,
    revision: int,
    document_id: int,
    note: str,
    actor_id: int,
) -> DossierApproval:
    stage = case.application_stage or ("preparing" if case.bid_decision == "bid" else "interested")
    if stage not in {"preparing", "ready"}:
        raise Conflict("Le contrôle final est disponible pendant la préparation du dossier.")
    lock_edit(session, case, revision)
    if not note.strip():
        raise Conflict("Renseignez votre conclusion de contrôle final.")
    manifest = build_manifest(session, case)
    issues = preflight(session, case, manifest, document_id)
    if issues:
        raise Conflict("Contrôle final impossible : " + " ".join(issues))
    approval = DossierApproval(
        case_id=case.id,
        revision=manifest["revision"],
        fingerprint=fingerprint(manifest),
        prepared_document_id=document_id,
        manifest=manifest,
        note=note.strip(),
        approved_by=actor_id,
    )
    session.add(approval)
    session.flush()
    audit(
        session,
        "dossier.final_approved",
        "company_case",
        str(case.id),
        actor_type="user",
        actor_id=str(actor_id),
        meta={
            "company_id": case.company_id,
            "approval_id": approval.id,
            "revision": approval.revision,
        },
    )
    return approval


def require_approval(session: Session, case: CompanyCase) -> None:
    if not dossier_state(session, case)["approved"]:
        raise Conflict(
            "Terminez la checklist et validez le contrôle final "
            "dans l’espace Dossier de candidature."
        )


def export_package(session: Session, case: CompanyCase, approval_id: int) -> Iterator[bytes]:
    approval = session.get(DossierApproval, approval_id)
    if approval is None or approval.case_id != case.id:
        raise NotFound("Validation de dossier introuvable.")
    stream = cast(BinaryIO, SpooledTemporaryFile(max_size=1024 * 1024))
    try:
        with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        **approval.manifest,
                        "approval": {
                            "id": approval.id,
                            "note": approval.note,
                            "approved_by": approval.approved_by,
                            "created_at": approval.created_at.isoformat(),
                            "prepared_document_id": approval.prepared_document_id,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            for data in approval.manifest["documents"]:
                doc = get_document(session, case, data["id"])
                if hashlib.sha256(doc.content).hexdigest() != data["sha256"]:
                    raise Conflict("L’intégrité d’un document archivé ne peut pas être confirmée.")
                archive.writestr(f"{doc.kind}/document-{doc.id}-v{doc.version}.pdf", doc.content)
        stream.seek(0)
    except Exception:
        stream.close()
        raise

    def chunks() -> Iterator[bytes]:
        try:
            while chunk := stream.read(64 * 1024):
                yield chunk
        finally:
            stream.close()

    return chunks()
