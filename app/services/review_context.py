"""An immutable, bounded reading packet for one company and one SAM notice."""

import hashlib
import io
import json
from typing import Any

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Company, CompanyCase, CompanyDocument, Opportunity
from app.services.dossiers import all_documents, latest_documents

MAX_TEXT = 240000
MAX_PAGES = 100


def context_manifest(session: Session, case: CompanyCase) -> dict[str, Any]:
    company = session.get_one(Company, case.company_id)
    opp = session.get_one(Opportunity, case.notice_id)
    docs = [
        d for d in latest_documents(all_documents(session, case.id)) if d.kind == "solicitation"
    ]
    inventory = [
        {
            "id": d.id,
            "kind": d.kind,
            "title": d.title,
            "expires_on": d.expires_on.isoformat() if d.expires_on else None,
            "review_status": d.review_status,
        }
        for d in session.scalars(
            select(CompanyDocument)
            .where(CompanyDocument.company_id == company.id)
            .order_by(CompanyDocument.id)
        )
    ]
    return {
        "company_id": company.id,
        "company_name": company.name,
        "profile": company.profile,
        "statement": company.statement,
        "statement_mode": company.statement_mode,
        "statement_pdf_hash": hashlib.sha256(company.statement_pdf).hexdigest()
        if company.statement_mode == "pdf" and company.statement_pdf
        else None,
        "opportunity": {
            "notice_id": opp.notice_id,
            "title": opp.title,
            "naics_code": opp.naics_code,
            "psc_code": opp.classification_code,
            "set_aside_category": opp.set_aside_category,
            "set_aside_code": opp.set_aside_code,
            "pop_state": opp.pop_state,
            "active": opp.active,
            "deadline": opp.response_deadline_utc.isoformat()
            if opp.response_deadline_utc
            else None,
            "description": opp.description_text,
            "raw": {k: v for k, v in opp.raw_payload.items() if k != "_synced_at"},
        },
        "consultation_documents": [
            {"id": d.id, "title": d.title, "sha256": d.sha256} for d in docs
        ],
        "contractor_document_inventory": inventory,
    }


def context_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()


def build_snapshot(session: Session, case: CompanyCase, manifest: dict[str, Any]) -> dict[str, Any]:
    sources: list[dict[str, str]] = []
    warnings: list[str] = []
    remaining = MAX_TEXT

    def add(source_id: str, title: str, text: str) -> None:
        nonlocal remaining
        if not text.strip():
            warnings.append("Texte non extractible : " + title + ". OCR non effectué.")
            return
        chunk = text[: min(remaining, 60000)]
        if len(chunk) < len(text):
            warnings.append("Source partiellement incluse : " + title)
        if chunk:
            sources.append({"id": source_id, "title": title, "text": chunk})
            remaining -= len(chunk)

    def structured(source_id: str, title: str, data: Any) -> None:
        add(source_id, title, json.dumps(data, ensure_ascii=False, default=str))

    structured("opportunity", "Avis SAM et description", manifest["opportunity"])
    structured("company_profile", "Profil de l’entreprise", manifest["profile"])
    structured("company_statement", "Présentation de l’entreprise", manifest["statement"])
    structured(
        "contractor_inventory",
        "Inventaire des justificatifs (pas leur contenu)",
        manifest["contractor_document_inventory"],
    )
    pages_read = 0

    def pdf(source_id: str, title: str, content: bytes) -> None:
        nonlocal pages_read
        try:
            reader = PdfReader(io.BytesIO(content))
            for number, page in enumerate(reader.pages, 1):
                if pages_read >= MAX_PAGES or remaining <= 0:
                    warnings.append("Limite de lecture atteinte : " + title)
                    break
                pages_read += 1
                add(
                    f"{source_id}:page:{number}",
                    f"{title} — page {number}",
                    page.extract_text() or "",
                )
        except Exception:
            warnings.append("PDF non lisible : " + title + ". Vérification manuelle nécessaire.")

    company = session.get_one(Company, case.company_id)
    if company.statement_mode == "pdf" and company.statement_pdf:
        pdf("statement_pdf", "Company Statement PDF", company.statement_pdf)
    for doc in latest_documents(all_documents(session, case.id)):
        if doc.kind == "solicitation":
            pdf(f"document:{doc.id}", doc.title, doc.content)
    if not manifest["consultation_documents"]:
        warnings.append("Aucune pièce de consultation jointe : examen fondé sur l’avis disponible.")
    return {"sources": sources, "warnings": warnings, "schema_version": "examination.v1"}
