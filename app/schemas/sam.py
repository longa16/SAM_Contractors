from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class SamPage(BaseModel):
    totalRecords: int = Field(ge=0)
    opportunitiesData: list[dict[str, Any]] = Field(default_factory=list)


class NormalizedOpportunity(BaseModel):
    notice_id: str = Field(min_length=1, max_length=128)
    solicitation_number: str
    title: str
    type: str
    description_url: str | None
    point_of_contact: list[dict[str, Any]]
    additional_info_link: str | None
    agency_path: str
    agency_code: str
    organization_type: str
    office_address: str
    naics_code: str
    classification_code: str
    set_aside_code: str | None
    set_aside_label: str | None
    set_aside_category: str
    posted_date: date
    response_deadline_utc: datetime | None
    response_deadline_raw: str | None
    active: bool
    place_of_performance_raw: str | None
    pop_state: str | None
    resource_links: list[str]
    raw_payload: dict[str, Any]
    synced_at: datetime
