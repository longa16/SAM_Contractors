"""Evaluate local triage against human labels; never manufacture missing gold labels."""

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from app.agents.opportunity_analyst import triage
from app.db.models import Opportunity
from app.sam.normalize import format_opp
from app.schemas.company import CompanyProfile


class LabeledOpportunity(BaseModel):
    opportunity: dict[str, Any]
    recommendation: Literal["RECOMMENDED", "REVIEW", "PASS"]
    reviewer: str = Field(min_length=1)


def metrics(expected: list[str], actual: list[str]) -> dict[str, float | None]:
    if len(expected) != len(actual) or not expected:
        raise ValueError("Nonempty aligned evaluation lists are required")
    positives = sum(value == "RECOMMENDED" for value in expected)
    hits = sum(
        truth == predicted == "RECOMMENDED"
        for truth, predicted in zip(expected, actual, strict=True)
    )
    return {
        "accuracy": sum(a == b for a, b in zip(expected, actual, strict=True)) / len(expected),
        "recommended_recall": hits / positives if positives else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate local screening against >=30 human-labeled notices"
    )
    parser.add_argument("labels", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    rows = [
        LabeledOpportunity.model_validate_json(line)
        for line in args.labels.read_text().splitlines()
        if line.strip()
    ]
    if len(rows) < 30 or len({row.opportunity.get("noticeId") for row in rows}) != len(rows):
        raise SystemExit("Au moins 30 avis distincts étiquetés par un humain sont requis.")
    profile = CompanyProfile.model_validate(yaml.safe_load(args.profile.read_text()))
    now = datetime.now(UTC)
    actual = [
        triage(
            Opportunity(**format_opp(row.opportunity, now).model_dump()), profile, now
        ).recommendation
        for row in rows
    ]
    report = metrics([row.recommendation for row in rows], actual)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.info(json.dumps({"mode": "local_triage", "count": len(rows), **report}))
    if report["recommended_recall"] is None or report["recommended_recall"] < 0.90:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
