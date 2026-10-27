import json
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.services import hermes_runtime as runtime


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "handoff",
        "cancel",
        "timeout",
        "malformed",
        "launch_error",
        "provider_quota",
        "import_error",
        "crash",
        "unsafe_diagnostic",
    ],
)
def test_runtime_bounds_process_and_does_not_inherit_application_secrets(
    settings, monkeypatch, caplog, outcome
):
    settings.hermes_python = Path(sys.executable)
    settings.hermes_api_key = SecretStr("test-model-only-key")
    monkeypatch.setenv("JWT_SECRET", "APP-JWT-MUST-NOT-BE-INHERITED")
    monkeypatch.setenv("DATABASE_URL", "PRIVATE-DATABASE")
    monkeypatch.setenv("SAM_API_KEY", "PRIVATE-SAM")
    monkeypatch.setenv("GROQ_API_KEY", "OTHER-PROVIDER-CREDENTIAL")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:80")
    processes = []

    class Process:
        def __init__(self, command, **kwargs):
            if outcome == "launch_error":
                raise OSError("PRIVATE-DATABASE")
            root = Path(command[-1])
            payload = json.loads((root / "input.json").read_text())
            assert "report_schema" in payload
            env = kwargs["env"]
            assert env["SAM_HERMES_API_KEY"] == "test-model-only-key"
            assert env["HTTPS_PROXY"] == "http://proxy.example.test:80"
            assert env["PYTHONUTF8"] == "1"
            assert env["PYTHONIOENCODING"] == "utf-8"
            assert not {"JWT_SECRET", "DATABASE_URL", "SAM_API_KEY", "GROQ_API_KEY"}.intersection(
                env
            )
            assert kwargs["cwd"] == root
            self.returncode = None if outcome in ["cancel", "timeout", "handoff"] else 0
            if outcome == "crash":
                self.returncode = 1
            self.killed = False
            if outcome in ["success", "handoff"]:
                (root / "result.json").write_text('{"report": {}, "tool_calls": []}')
            if outcome == "provider_quota":
                (root / "result.json").write_text('{"error_code": "rate_limited"}')
            if outcome == "malformed":
                (root / "result.json").write_text("invalid-json")
            if outcome == "import_error":
                (root / "result.json").write_text(
                    json.dumps(
                        {
                            "error_code": "runtime_import_error",
                            "diagnostic": {
                                "phase": "imports",
                                "exception_kind": "ModuleNotFoundError",
                            },
                        }
                    )
                )
            if outcome == "unsafe_diagnostic":
                (root / "result.json").write_text(
                    json.dumps(
                        {
                            "error_code": "PRIVATE-SAM",
                            "diagnostic": {"phase": "PRIVATE-SAM", "exception_kind": "PRIVATE-SAM"},
                        }
                    )
                )
            processes.append(self)

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout):
            return self.returncode

    monkeypatch.setattr(runtime.subprocess, "Popen", Process)
    if outcome == "timeout":
        times = iter([0, 10000])
        monkeypatch.setattr(runtime.time, "monotonic", lambda: next(times))
    result, error = runtime.run_hermes(
        settings, {"sources": [], "warnings": []}, "test-model", lambda: outcome == "cancel"
    )
    if outcome in ["success", "handoff"]:
        assert result == {"report": {}, "tool_calls": []} and error is None
    elif outcome == "provider_quota":
        assert result is None and error == "rate_limited"
    elif outcome == "import_error":
        assert result is None and error == "runtime_import_error"
        assert any(r.fields.get("phase") == "imports" for r in caplog.records)
    elif outcome in ["crash", "launch_error"]:
        assert result is None and error == "runtime_process_error"
    elif outcome == "malformed":
        assert result is None and error == "invalid_report"
    elif outcome in ["cancel", "timeout"]:
        assert error == ("interrupted" if outcome == "cancel" else "timeout")
        assert processes[0].killed
    else:
        assert result is None and error == "runtime_error"
    fields = str([getattr(r, "fields", {}) for r in caplog.records])
    assert "PRIVATE-SAM" not in fields and "PRIVATE-DATABASE" not in fields


def test_custom_endpoint_never_receives_the_groq_fallback_key(settings, monkeypatch):
    settings.hermes_base_url = "https://other-provider.example.test/v1"
    settings.hermes_api_key = None

    def deny(*args, **kwargs):
        raise AssertionError("No child process may receive this credential")

    monkeypatch.setattr(runtime.subprocess, "Popen", deny)
    assert runtime.run_hermes(
        settings, {"sources": [], "warnings": []}, "model", lambda: False
    ) == (None, "runtime_error")
