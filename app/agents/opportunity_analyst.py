from datetime import datetime
from typing import Literal

from app.db.models import Opportunity
from app.schemas.company import CompanyProfile
from app.schemas.screening import Dimension, Factor, ScreeningLLMOutput, ScreeningOutput

CERTIFICATIONS = {
    "eight_a": "8a",
    "hubzone": "hubzone",
    "sdvosb": "sdvosb",
    "wosb": "wosb",
    "edwosb": "edwosb",
}


def weighted_score(dimensions: dict[str, Dimension], profile: CompanyProfile) -> float:
    weights = profile.screening.weights
    denominator = sum(weights[key] for key in dimensions)
    if not denominator:
        return 0
    return round(
        sum(weights[key] * value.score for key, value in dimensions.items()) / denominator, 2
    )


def recommendation(
    score: float, profile: CompanyProfile, blocked: bool
) -> Literal["RECOMMENDED", "REVIEW", "PASS"]:
    if blocked:
        return "PASS"
    if score >= profile.screening.recommended:
        return "RECOMMENDED"
    if score >= profile.screening.review:
        return "REVIEW"
    return "PASS"


def triage(opp: Opportunity, profile: CompanyProfile, now: datetime) -> ScreeningOutput:
    target = profile.targeting
    positive: list[Factor] = []
    blocking: list[Factor] = []
    if opp.naics_code in target.naics_primary:
        capacity = 100
    elif opp.naics_code in target.naics_secondary:
        capacity = 60
    elif any(
        opp.naics_code[:4] == code[:4] for code in target.naics_primary + target.naics_secondary
    ):
        capacity = 30
    else:
        capacity = 0
    if opp.classification_code in target.psc_codes:
        capacity = min(capacity + 10, 100)
    geography = 50 if not opp.pop_state else (100 if opp.pop_state in target.service_states else 0)
    if not opp.pop_state:
        positive.append(Factor(text_fr="Lieu non précisé : compatibilité géographique à vérifier."))
    certifications = {value.lower() for value in profile.company.certifications}
    category = opp.set_aside_category
    eligible = category == "none" or (
        category == "small_business" and profile.company.small_business
    )
    if category in CERTIFICATIONS:
        eligible = CERTIFICATIONS[category] in certifications
    if not eligible:
        blocking.append(
            Factor(text_fr=f"Set-aside {opp.set_aside_code} : éligibilité non démontrée.")
        )
    days = (
        None
        if not opp.response_deadline_utc
        else (opp.response_deadline_utc - now).total_seconds() / 86400
    )
    if days is not None and days < target.min_days_to_respond:
        blocking.append(Factor(text_fr="Délai de réponse inférieur au minimum configuré."))
    timeline = (
        50.0
        if days is None
        else max(
            0,
            min(
                100,
                100
                * (days - target.min_days_to_respond)
                / (target.ideal_days - target.min_days_to_respond),
            ),
        )
    )
    if not opp.active:
        blocking.append(Factor(text_fr="Opportunité inactive."))
    if capacity:
        positive.append(Factor(text_fr="Capacité NAICS/PSC cohérente avec le profil déclaré."))
    dimensions = {
        "capacity": Dimension(score=capacity),
        "geography": Dimension(score=geography, unknown=not opp.pop_state),
        "timeline": Dimension(score=timeline, unknown=days is None),
        "set_aside": Dimension(score=100 if eligible else 0),
        "size": Dimension(score=50, unknown=True),
    }
    score = weighted_score(dimensions, profile)
    return ScreeningOutput(
        score=score,
        recommendation=recommendation(score, profile, bool(blocking)),
        dimension_scores=dimensions,
        positive_factors=positive,
        blocking_factors=blocking,
        hard_blocked=bool(blocking),
    )


def merge_analysis(
    stage1: ScreeningOutput, analysis: ScreeningLLMOutput, profile: CompanyProfile
) -> ScreeningOutput:
    result = stage1.model_copy(deep=True)
    result.stage = 2
    result.positive_factors.extend(analysis.positive_factors)
    result.blocking_factors.extend(analysis.blocking_factors)
    result.estimated_value_usd = analysis.estimated_value_usd
    dimensions = result.dimension_scores
    # Undocumented qualifications remain unknown; an LLM cannot certify the company.
    dimensions["licenses"] = Dimension(score=50, unknown=True)
    dimensions["subcontracting"] = Dimension(
        score=50, unknown=not analysis.subcontracting_constraints
    )
    dimensions["risk"] = Dimension(
        score={"low": 100, "medium": 50, "high": 0}.get(analysis.scope_complexity or "", 50),
        unknown=analysis.scope_complexity is None,
    )
    value = analysis.estimated_value_usd
    dimensions["size"] = Dimension(
        score=50
        if value is None
        else (
            100
            if profile.targeting.contract_value_min_usd
            <= value
            <= profile.targeting.contract_value_max_usd
            else 0
        ),
        unknown=value is None,
    )
    bond = analysis.bonding_required_usd
    dimensions["bonding"] = Dimension(
        score=50 if bond is None else (100 if bond <= profile.targeting.max_bonding_usd else 0),
        unknown=bond is None,
    )
    if bond is not None and bond > profile.targeting.max_bonding_usd:
        result.hard_blocked = True
        result.blocking_factors.append(
            Factor(text_fr="Cautionnement requis supérieur à la capacité déclarée.")
        )
    result.hard_blocked = result.hard_blocked or bool(analysis.blocking_factors)
    result.score = weighted_score(dimensions, profile)
    result.recommendation = recommendation(result.score, profile, result.hard_blocked)
    return result
