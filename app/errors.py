class DomainError(Exception):
    code = "domain_error"
    status_code = 400


class ConfigurationError(DomainError):
    code = "configuration_error"
    status_code = 503


class SamAPIError(DomainError):
    code = "sam_api_error"
    status_code = 502

    MESSAGES = {
        "cancelled": "Synchronisation interrompue ; progression conservée pour la reprise.",
        "http_rejected": "SAM.gov a refusé la requête. Vérifiez les droits, la clé et le quota.",
        "rate_limited": "Quota ou limite de débit SAM.gov atteint ; réessayez plus tard.",
        "upstream_unavailable": "SAM.gov est temporairement indisponible.",
        "network_error": "Connexion à SAM.gov impossible (réseau, proxy ou délai dépassé).",
        "invalid_response": "La réponse SAM.gov ne correspond pas au format attendu.",
        "incomplete_page": "SAM.gov a renvoyé une page vide avant la fin des résultats.",
        "page_limit": "Plafond de pagination atteint ; la fenêtre n’a pas été validée.",
        "unknown": "Échec SAM.gov ; consultez le diagnostic du serveur.",
    }

    def __init__(
        self,
        message: str,
        *,
        reason: str = "unknown",
        http_status: int | None = None,
        attempts: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason if reason in self.MESSAGES else "unknown"
        self.http_status = http_status if http_status and 100 <= http_status <= 599 else None
        self.attempts = attempts

    def diagnostic(self) -> dict[str, str | int | None]:
        # Only fixed messages and allowlisted metadata; never URLs, response bodies or keys.
        return {
            "reason": self.reason,
            "http_status": self.http_status,
            "attempts": self.attempts,
            "message": self.MESSAGES[self.reason],
        }


class SyncBusy(DomainError):
    code = "sync_busy"
    status_code = 409


class InvalidTransition(DomainError):
    code = "invalid_transition"
    status_code = 409


class NotFound(DomainError):
    code = "not_found"
    status_code = 404


class Forbidden(DomainError):
    code = "forbidden"
    status_code = 403


class Unauthorized(DomainError):
    code = "unauthorized"
    status_code = 401


class Conflict(DomainError):
    code = "conflict"
    status_code = 409


class LLMOutputError(DomainError):
    code = "llm_output_error"
    status_code = 502


class LLMUnavailable(DomainError):
    code = "llm_unavailable"
    status_code = 503


class InitialSyncComplete(Exception):
    """An initial-only caller lost the race to a successful bootstrap."""
