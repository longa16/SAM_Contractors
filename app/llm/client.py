import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.errors import LLMUnavailable


@dataclass(frozen=True)
class Completion:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0


class LLMClient(Protocol):
    def complete(
        self, system: str, user: str, *, schema: dict[str, Any], temperature: float, max_tokens: int
    ) -> Completion: ...


class GroqClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.failures = 0
        self.blocked_until = 0.0
        self.lock = Lock()

    def complete(
        self, system: str, user: str, *, schema: dict[str, Any], temperature: float, max_tokens: int
    ) -> Completion:
        if not self.settings.groq_api_key:
            raise LLMUnavailable("Clé Groq non configurée.")
        with self.lock:
            if time.monotonic() < self.blocked_until:
                raise LLMUnavailable("Analyse IA temporairement suspendue.")
        for attempt in range(self.settings.llm_retry_attempts):
            try:
                with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
                    response = client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": "Bearer "
                            + self.settings.groq_api_key.get_secret_value()
                        },
                        json={
                            "model": self.settings.llm_model,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "response_format": {"type": "json_object"},
                        },
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.TransportError("Retryable model provider failure")
                if response.status_code != 200:
                    break
                payload = response.json()
                completion = Completion(
                    str(payload["choices"][0]["message"]["content"]),
                    int(payload.get("usage", {}).get("prompt_tokens", 0)),
                    int(payload.get("usage", {}).get("completion_tokens", 0)),
                )
                with self.lock:
                    self.failures = 0
                return completion
            except (httpx.TransportError, ValueError, KeyError, IndexError, TypeError):
                if attempt + 1 < self.settings.llm_retry_attempts:
                    time.sleep(min(2**attempt, 8))
        with self.lock:
            self.failures += 1
            if self.failures >= self.settings.llm_failure_limit:
                self.blocked_until = time.monotonic() + self.settings.llm_circuit_seconds
        raise LLMUnavailable("Fournisseur IA indisponible ; analyse reportée.")
