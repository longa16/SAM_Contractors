from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    auto_initial_sync: bool = True
    scheduled_sync_enabled: bool = False
    bootstrap_chunk_days: int = Field(default=7, ge=1, le=31)

    sam_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    database_url: str = f"sqlite:///{ROOT / 'data/app.db'}"
    log_level: str = "INFO"
    sam_timeout_seconds: float = Field(default=60, gt=0)
    sam_retry_attempts: int = Field(default=3, ge=1, le=5)
    sam_retry_wait_seconds: float = Field(default=1, ge=0)
    sam_page_size: int = Field(default=1000, ge=1, le=1000)
    sam_max_pages: int = Field(default=50, ge=1)
    sam_window_days: int = Field(default=365, ge=1, le=365)
    sam_user_agent: str = "GovContractAI/0.1 (SAM Opportunities API client)"
    sync_lock_minutes: int = Field(default=30, ge=1)
    sync_cron_hours: str = "0,8,16"
    sync_catchup_hours: int = Field(default=8, ge=1)
    retention_days: int = Field(default=30, ge=30)
    screening_stage1_threshold: int = Field(default=40, ge=0, le=100)
    screening_stage2_daily_cap: int = Field(default=100, ge=0)
    llm_model: str = "llama-3.3-70b-versatile"

    jwt_secret: SecretStr | None = None
    jwt_expiry_minutes: int = Field(default=30, ge=1, le=120)
    jwt_issuer: str = "govcontractai"
    max_desc_chars: int = Field(default=24000, ge=100, le=100000)
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_daily_token_budget: int = Field(default=1000000, ge=0)
    llm_max_tokens: int = Field(default=2000, ge=100)
    llm_failure_limit: int = Field(default=5, ge=1)
    llm_circuit_seconds: int = Field(default=60, ge=1)
    llm_retry_attempts: int = Field(default=3, ge=1, le=5)

    public_app_url: str = Field(default="http://127.0.0.1:8000", pattern=r"^https?://[^?#\s]+$")
    mail_mode: Literal["local", "smtp"] = "local"
    mail_from: str = "GovContractAI <no-reply@example.test>"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl", "plain"] = "starttls"
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_timeout_seconds: int = Field(default=15, ge=1, le=60)
    invite_expiry_hours: int = Field(default=24, ge=1, le=72)
    reset_expiry_minutes: int = Field(default=30, ge=5, le=120)
