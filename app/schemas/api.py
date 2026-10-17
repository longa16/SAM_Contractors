from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.schemas.company import CompanyProfile


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    profile: CompanyProfile | None = None


class UserCreate(BaseModel):
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
    password: SecretStr
    role: Literal["admin", "business_developer", "ops_manager"]
    company_id: int | None = None

    @field_validator("password")
    @classmethod
    def password_length(cls, value: SecretStr) -> SecretStr:
        if not 12 <= len(value.get_secret_value()) <= 200:
            raise ValueError("Password must contain 12–200 characters")
        return value


class Decision(BaseModel):
    decision: Literal["bid", "no_bid"]
    reason: str | None = Field(default=None, max_length=1000)


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Comment cannot be blank")
        return value.strip()
