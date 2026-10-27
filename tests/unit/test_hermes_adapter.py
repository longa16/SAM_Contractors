import json

import pytest

from scripts import hermes_adapter as adapter


@pytest.mark.parametrize(
    "error,phase,code",
    [
        (ModuleNotFoundError("private dependency"), "imports", "runtime_import_error"),
        (ImportError("private dependency"), "imports", "runtime_import_error"),
        (TypeError("unexpected argument"), "initialization", "runtime_incompatible"),
        (RuntimeError("unexpected tool surface"), "initialization", "runtime_incompatible"),
        ("Error 401 with private key", "conversation", "provider_auth"),
        ("Error 403 with private key", "conversation", "provider_auth"),
        ("404 unknown model", "conversation", "model_unavailable"),
        ("model_decommissioned", "conversation", "model_unavailable"),
        ("429 rate limit", "conversation", "rate_limited"),
        ("quota exceeded", "conversation", "rate_limited"),
        ("400 invalid request", "conversation", "provider_request"),
        ("422 invalid parameters", "conversation", "provider_request"),
        ("Connection error", "conversation", "provider_connection"),
        ("certificate verify failed", "conversation", "provider_connection"),
        (TimeoutError("private URL"), "conversation", "timeout"),
        (ValueError("not a report"), "report", "invalid_report"),
        (RuntimeError("unknown private error"), "conversation", "runtime_error"),
    ],
)
def test_adapter_classifies_errors_without_returning_raw_provider_content(error, phase, code):
    assert adapter.error_code(error, phase) == code


@pytest.mark.parametrize("delivered", [True, False])
def test_adapter_catches_startup_failure_without_exposing_secrets(tmp_path, monkeypatch, delivered):
    def fail(root, progress):
        progress["phase"] = "imports"
        if delivered:
            (root / "result.json").write_text('{"report": "delivered"}')
        raise ModuleNotFoundError("SECRET-KEY and PRIVATE-COMPANY-DATA")

    monkeypatch.setattr(adapter, "execute", fail)
    adapter.main(tmp_path)
    output = (tmp_path / "result.json").read_text()
    assert "SECRET-KEY" not in output and "PRIVATE-COMPANY-DATA" not in output
    result = json.loads(output)
    if delivered:
        assert result == {"report": "delivered"}
    else:
        assert result == {
            "error_code": "runtime_import_error",
            "diagnostic": {"phase": "imports", "exception_kind": "ModuleNotFoundError"},
        }
