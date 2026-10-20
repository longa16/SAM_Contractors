from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CompanyIdentity(StrictModel):
    legal_name: str = Field(min_length=1, max_length=200)
    small_business: bool
    certifications: list[str] = Field(default_factory=list)
    sam_registration_expires: str | None = None


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


class CompanyProfile(StrictModel):
    company: CompanyIdentity
    targeting: Targeting
    screening: ScreeningPolicy = Field(default_factory=ScreeningPolicy)


class CompanyStatement(StrictModel):
    tagline: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=10000)
    capabilities: str = Field(default="", max_length=10000)
    achievements: str = Field(default="", max_length=20000)
    differentiators: str = Field(default="", max_length=10000)
    resources: str = Field(default="", max_length=10000)
    contact_name: str = Field(default="", max_length=200)
    contact_email: str = Field(default="", max_length=254)
    contact_phone: str = Field(default="", max_length=100)
