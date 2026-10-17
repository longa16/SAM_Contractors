import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import pytest

from app.agents.opportunity_analyst import merge_analysis, recommendation, triage
from app.db.models import Opportunity
from app.sam.normalize import format_opp
from app.schemas.company import CompanyProfile, ScreeningPolicy
from app.schemas.screening import ScreeningLLMOutput


def test_fifty_real_anonymized_samples_triaged_offline_under_two_seconds(profile):
    records = json.loads((Path(__file__).parents[1] / "fixtures/sam_sample.json").read_text())
    now = datetime.now(UTC)
    start = perf_counter()
    for raw in records:
        normalized = format_opp(raw, now)
        result = triage(Opportunity(**normalized.model_dump()), profile, now)
        assert 0 <= result.score <= 100
        assert result.positive_factors or result.blocking_factors or result.dimension_scores
    assert len(records) >= 50
    assert perf_counter() - start < 2


@pytest.mark.parametrize(
    ("category", "code"), [("eight_a", "8A"), ("other", "UNKNOWN"), ("edwosb", "EDWOSB")]
)
def test_restricted_set_asides_block_ineligible_company(profile, seeded_opp, category, code):
    seeded_opp.set_aside_category, seeded_opp.set_aside_code = category, code
    result = triage(seeded_opp, profile, datetime.now(UTC))
    assert result.hard_blocked and result.recommendation == "PASS"
    assert any("Set-aside" in f.text_fr for f in result.blocking_factors)


def test_short_deadline_blocks_and_unknown_geography_is_neutral(profile, seeded_opp):
    now = datetime.now(UTC)
    seeded_opp.response_deadline_utc = now + timedelta(days=1)
    seeded_opp.pop_state = None
    result = triage(seeded_opp, profile, now)
    assert result.hard_blocked
    assert result.dimension_scores["geography"].score == 50
    assert result.dimension_scores["geography"].unknown


@pytest.mark.parametrize(
    ("naics", "expected"), [("238220", 100), ("238210", 60), ("238299", 30), ("111111", 0)]
)
def test_capacity_table(profile, seeded_opp, naics, expected):
    profile.targeting.naics_secondary = ["238210"]
    seeded_opp.naics_code = naics
    result = triage(seeded_opp, profile, datetime.now(UTC))
    assert result.dimension_scores["capacity"].score == expected


def test_merge_preserves_unknown_values_and_blocks_excessive_bond(profile, seeded_opp):
    initial = triage(seeded_opp, profile, datetime.now(UTC))
    analysis = ScreeningLLMOutput(
        required_licenses=None,
        required_insurance=None,
        bonding_required_usd=None,
        subcontracting_constraints=None,
        estimated_value_usd=None,
        scope_complexity=None,
        risks=[],
        positive_factors=[],
        blocking_factors=[],
        capability_keywords_matched=[],
    )
    result = merge_analysis(initial, analysis, profile)
    assert result.dimension_scores["bonding"].unknown
    assert result.estimated_value_usd is None
    analysis.bonding_required_usd = profile.targeting.max_bonding_usd + 1
    result = merge_analysis(initial, analysis, profile)
    assert result.hard_blocked and result.recommendation == "PASS"
    assert recommendation(10, profile, False) == "PASS"
    assert recommendation(50, profile, False) == "REVIEW"
    assert recommendation(90, profile, False) == "RECOMMENDED"


def test_profile_rejects_invalid_ranges_and_weights(profile):
    raw = profile.model_dump()
    raw["targeting"]["ideal_days"] = 1
    with pytest.raises(ValueError):
        CompanyProfile.model_validate(raw)
    with pytest.raises(ValueError):
        ScreeningPolicy(weights={})
    with pytest.raises(ValueError):
        ScreeningPolicy(review=80, recommended=70)
