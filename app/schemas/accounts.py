from typing import Literal

from pydantic import Field, SecretStr, field_validator

from app.schemas.company import StrictModel


class Invitation(StrictModel):
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
    company_id: int = Field(gt=0)
    role: Literal["business_developer", "ops_manager"] = "ops_manager"


class MemberUpdate(StrictModel):
    is_active: bool
    role: Literal["business_developer", "ops_manager"]


class Password(StrictModel):
    password: SecretStr

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: SecretStr) -> SecretStr:
        if not 12 <= len(value.get_secret_value()) <= 200 or not value.get_secret_value().strip():
            raise ValueError("Use 12 to 200 characters")
        return value


class AcceptAction(Password):
    token: str = Field(min_length=20, max_length=256)


class ChangePassword(Password):
    current_password: SecretStr


class ForgotPassword(StrictModel):
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
