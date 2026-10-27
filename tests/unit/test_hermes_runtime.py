import json
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.services import hermes_runtime as runtime


@pytest.mark.parametrize(
    "outcome",
    ["success", "handoff", "cancel", "timeout", "malformed", "launch_error", "provider_quota"],
)
def test_runtime_bounds_process_and_does_not_inherit_application_secrets(
    settings, monkeypatch, outcome
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
            assert not {"JWT_SECRET", "DATABASE_URL", "SAM_API_KEY", "GROQ_API_KEY"}.intersection(
                env
            )
            assert kwargs["cwd"] == root
            self.returncode = None if outcome in ["cancel", "timeout", "handoff"] else 0
            self.killed = False
            if outcome in ["success", "handoff"]:
                (root / "result.json").write_text('{"report": {}, "tool_calls": []}')
            if outcome == "provider_quota":
                (root / "result.json").write_text('{"error_code": "rate_limited"}')
            if outcome == "malformed":
                (root / "result.json").write_text("invalid-json")
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
    elif outcome in ["cancel", "timeout"]:
        assert error == ("interrupted" if outcome == "cancel" else "timeout")
        assert processes[0].killed
    else:
        assert result is None and error == "runtime_error"


def test_custom_endpoint_never_receives_the_groq_fallback_key(settings, monkeypatch):
    settings.hermes_base_url = "https://other-provider.example.test/v1"
    settings.hermes_api_key = None

    def deny(*args, **kwargs):
        raise AssertionError("No child process may receive this credential")

    monkeypatch.setattr(runtime.subprocess, "Popen", deny)
    assert runtime.run_hermes(
        settings, {"sources": [], "warnings": []}, "model", lambda: False
    ) == (None, "runtime_error")
