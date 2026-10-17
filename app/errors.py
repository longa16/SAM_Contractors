class DomainError(Exception):
    code = "domain_error"
    status_code = 400


class ConfigurationError(DomainError):
    code = "configuration_error"
    status_code = 503


class SamAPIError(DomainError):
    code = "sam_api_error"
    status_code = 502


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
