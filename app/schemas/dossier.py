from typing import Literal

from pydantic import Field, field_validator

from app.schemas.company import StrictModel

DocumentKind = Literal["solicitation", "prepared", "supporting"]


class ImportDocument(StrictModel):
    expected_revision: int = Field(ge=0)
    source_document_id: int = Field(gt=0)
    previous_document_id: int | None = Field(default=None, gt=0)


class ChecklistCreate(StrictModel):
    expected_revision: int = Field(ge=0)
    label: str = Field(min_length=1, max_length=300)
    required: bool = True

    @field_validator("label")
    @classmethod
    def label_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe the requirement")
        return value.strip()


class ChecklistUpdate(StrictModel):
    expected_revision: int = Field(ge=0)
    status: Literal["pending", "validated", "waived"]
    note: str = Field(default="", max_length=2000)


class FinalApproval(StrictModel):
    expected_revision: int = Field(ge=0)
    prepared_document_id: int = Field(gt=0)
    note: str = Field(min_length=1, max_length=2000)
