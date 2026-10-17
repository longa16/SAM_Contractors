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
