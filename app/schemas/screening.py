from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Factor(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    text_fr: str = Field(min_length=1, max_length=500)
    evidence: str = Field(default="", max_length=200)


class ScreeningLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    required_licenses: list[str] | None
    required_insurance: list[str] | None
    bonding_required_usd: float | None = Field(ge=0)
    subcontracting_constraints: list[str] | None
    estimated_value_usd: float | None = Field(ge=0)
    scope_complexity: Literal["low", "medium", "high"] | None
    risks: list[str]
    positive_factors: list[Factor]
    blocking_factors: list[Factor]
    capability_keywords_matched: list[str]


class Dimension(BaseModel):
    score: float = Field(ge=0, le=100)
    unknown: bool = False


class ScreeningOutput(BaseModel):
    score: float = Field(ge=0, le=100)
    recommendation: Literal["RECOMMENDED", "REVIEW", "PASS"]
    dimension_scores: dict[str, Dimension]
    positive_factors: list[Factor]
    blocking_factors: list[Factor]
    hard_blocked: bool
    estimated_value_usd: float | None = None
    stage: int = 1
