import json
import subprocess

import pytest
from pydantic import SecretStr

from app.config import hermes_python_path
from app.services.agent_reviews import configuration_status, configured
from scripts import check_hermes


@pytest.mark.parametrize(
    "platform,suffix",
    [("win32", "Scripts/python.exe"), ("linux", "bin/python"), ("darwin", "bin/python")],
)
def test_default_python_matches_platform(platform, suffix):
    assert hermes_python_path(platform).as_posix().endswith(suffix)


@pytest.mark.parametrize("missing", ["disabled", "runtime_missing", "api_key_missing", "ready"])
def test_configuration_explains_missing_requirement_without_exposing_keys(
    settings, tmp_path, missing
):
    runtime = tmp_path / "python"
    runtime.touch()
    settings.hermes_enabled = missing != "disabled"
    settings.hermes_python = tmp_path / "absent" if missing == "runtime_missing" else runtime
    settings.hermes_api_key = (
        None if missing == "api_key_missing" else SecretStr("secret-NEVER-DISPLAY")
    )
    settings.groq_api_key = None
    result = configuration_status(settings)
    assert result["code"] == missing
    assert configured(settings) == (missing == "ready")
    assert "secret-NEVER-DISPLAY" not in json.dumps(result)


@pytest.mark.parametrize("installed", [True, False])
def test_offline_check_diagnoses_wrong_python_without_provider_calls(
    settings, tmp_path, monkeypatch, capsys, installed
):
    runtime = tmp_path / "python"
    runtime.touch()
    settings.hermes_enabled = True
    settings.hermes_python = runtime
    settings.hermes_api_key = SecretStr("secret-NEVER-DISPLAY")
    monkeypatch.setattr(check_hermes, "Settings", lambda: settings)

    def probe(command, **kwargs):
        assert "-I" in command
        assert "SAM_HERMES_API_KEY" not in kwargs["env"]
        assert kwargs["cwd"] == kwargs["env"]["HERMES_HOME"]
        return subprocess.CompletedProcess(command, 0, json.dumps({"installed": installed}), "")

    monkeypatch.setattr(check_hermes.subprocess, "run", probe)
    if installed:
        check_hermes.main()
    else:
        with pytest.raises(SystemExit):
            check_hermes.main()
    assert "secret-NEVER-DISPLAY" not in capsys.readouterr().out
