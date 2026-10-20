from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CompanyIdentity(StrictModel):
    legal_name: str = Field(min_length=1, max_length=200)
    small_business: bool
    certifications: list[str] = Field(default_factory=list)
    sam_registration_expires: str | None = None
    address: str = Field(default="", max_length=500)
    city: str = Field(default="", max_length=100)
    postal_code: str = Field(default="", max_length=20)
    country: str = Field(default="US", max_length=100)
    website: str = Field(default="", max_length=500)
    contact_email: str = Field(default="", max_length=254)
    contact_phone: str = Field(default="", max_length=100)
    uei: str = Field(default="", max_length=12, pattern=r"^([A-Za-z0-9]{12})?$")
    cage_code: str = Field(default="", max_length=5, pattern=r"^([A-Za-z0-9]{5})?$")
    service_categories: list[str] = Field(default_factory=list, max_length=30)


class Targeting(StrictModel):
    naics_primary: list[str] = Field(min_length=1)
    naics_secondary: list[str] = Field(default_factory=list)
    psc_codes: list[str] = Field(default_factory=list)
    service_states: list[str] = Field(min_length=1)
    min_days_to_respond: int = Field(default=5, ge=0)
    ideal_days: int = Field(default=21, ge=1)
    contract_value_min_usd: float = Field(default=0, ge=0)
    contract_value_max_usd: float = Field(gt=0)
    max_bonding_usd: float = Field(ge=0)

    @model_validator(mode="after")
    def valid_ranges(self) -> "Targeting":
        if self.ideal_days <= self.min_days_to_respond:
            raise ValueError("ideal_days must exceed min_days_to_respond")
        if self.contract_value_max_usd < self.contract_value_min_usd:
            raise ValueError("Contract value range is invalid")
        if any(len(n) != 6 or not n.isdigit() for n in self.naics_primary + self.naics_secondary):
            raise ValueError("NAICS must contain six digits")
        from app.sam.normalize import STATE_CODES

        if any(s not in STATE_CODES for s in self.service_states):
            raise ValueError("Unknown service state")
        return self


class ScreeningPolicy(StrictModel):
    stage1_threshold: float = Field(default=40, ge=0, le=100)
    stage2_daily_cap: int = Field(default=100, ge=0)
    recommended: float = Field(default=70, ge=0, le=100)
    review: float = Field(default=40, ge=0, le=100)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "capacity": 0.30,
            "geography": 0.15,
            "size": 0.10,
            "timeline": 0.10,
            "set_aside": 0.10,
            "licenses": 0.10,
            "bonding": 0.05,
            "subcontracting": 0.05,
            "risk": 0.05,
        }
    )

    @model_validator(mode="after")
    def valid_weights(self) -> "ScreeningPolicy":
        expected = {
            "capacity",
            "geography",
            "size",
            "timeline",
            "set_aside",
            "licenses",
            "bonding",
            "subcontracting",
            "risk",
        }
        if (
            set(self.weights) != expected
            or min(self.weights.values()) < 0
            or sum(self.weights.values()) <= 0
        ):
            raise ValueError("All nine dimensions need nonnegative weights with a positive sum")
        if self.review > self.recommended:
            raise ValueError("Recommendation thresholds are reversed")
        return self


class Credential(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    issuer: str = Field(default="", max_length=200)
    number: str = Field(default="", max_length=100)
    jurisdiction: str = Field(default="", max_length=100)
    expires_on: date | None = None


class Rate(StrictModel):
    service: str = Field(min_length=1, max_length=200)
    unit: Literal["hour", "day", "project", "sqft", "visit", "other"]
    amount_usd: float = Field(ge=0)
    notes: str = Field(default="", max_length=1000)


class Operations(StrictModel):
    employee_count: int | None = Field(default=None, ge=0)
    crew_count: int | None = Field(default=None, ge=0)
    concurrent_projects: int | None = Field(default=None, ge=0)
    equipment: str = Field(default="", max_length=5000)
    service_area_details: str = Field(default="", max_length=2000)
    availability: Literal["unknown", "available", "limited", "unavailable"] = "unknown"
    available_from: date | None = None
    availability_notes: str = Field(default="", max_length=2000)
    workers_comp_applicable: bool | None = None
    workers_comp_explanation: str = Field(default="", max_length=2000)
    licenses_applicable: bool | None = None
    licenses_explanation: str = Field(default="", max_length=2000)
    licenses: list[Credential] = Field(default_factory=list, max_length=30)
    certifications: list[Credential] = Field(default_factory=list, max_length=30)
    rates: list[Rate] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def applicability_reason(self) -> "Operations":
        if self.workers_comp_applicable is False and not self.workers_comp_explanation.strip():
            raise ValueError("Explain why workers compensation does not apply")
        if self.licenses_applicable is False and not self.licenses_explanation.strip():
            raise ValueError("Explain why licenses do not apply")
        if self.licenses and self.licenses_applicable is False:
            raise ValueError("Declared licenses conflict with non-applicability")
        return self


class Reference(StrictModel):
    customer: str = Field(min_length=1, max_length=200)
    project: str = Field(min_length=1, max_length=200)
    year: int | None = Field(default=None, ge=1900, le=2200)
    scope: str = Field(default="", max_length=3000)
    value_usd: float | None = Field(default=None, ge=0)
    contact: str = Field(default="", max_length=300)
    government: bool = False
    contract_number: str = Field(default="", max_length=100)


class CompanyProfile(StrictModel):
    company: CompanyIdentity
    targeting: Targeting
    screening: ScreeningPolicy = Field(default_factory=ScreeningPolicy)
    operations: Operations = Field(default_factory=Operations)


class CompanyStatement(StrictModel):
    government_experience: str = Field(default="", max_length=10000)
    references: list[Reference] = Field(default_factory=list, max_length=30)
    tagline: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=10000)
    capabilities: str = Field(default="", max_length=10000)
    achievements: str = Field(default="", max_length=20000)
    differentiators: str = Field(default="", max_length=10000)
    resources: str = Field(default="", max_length=10000)
    contact_name: str = Field(default="", max_length=200)
    contact_email: str = Field(default="", max_length=254)
    contact_phone: str = Field(default="", max_length=100)
