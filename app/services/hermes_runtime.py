"""Run the pinned Hermes engine in a fresh process, never inside the web interpreter."""

import json
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from sqlalchemy import Engine

from app.config import ROOT, Settings
from app.db.base import session_factory
from app.db.models import AgentReview
from app.schemas.agent_reviews import ExaminationReport
from app.services.agent_reviews import (
    ERRORS,
    claim,
    configuration_status,
    configured,
    finish,
    provider_key,
)

log = logging.getLogger(__name__)


def run_hermes(
    settings: Settings, snapshot: dict[str, Any], model: str, cancelled: Callable[[], bool]
) -> tuple[dict[str, Any] | None, str | None]:
    with tempfile.TemporaryDirectory(prefix="sam-hermes-") as directory:
        root = Path(directory)
        payload = {
            "snapshot": snapshot,
            "model": model,
            "base_url": settings.hermes_base_url,
            "max_iterations": settings.hermes_max_iterations,
            "max_tokens": settings.hermes_max_tokens,
            "timeout": settings.hermes_timeout_seconds,
            "report_schema": ExaminationReport.model_json_schema(),
        }
        (root / "input.json").write_text(json.dumps(payload), encoding="utf-8")
        key = provider_key(settings)
        if key is None or not key.get_secret_value().strip():
            return None, "runtime_error"
        env = {
            name: os.environ[name]
            for name in [
                "PATH",
                "SYSTEMROOT",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "REQUESTS_CA_BUNDLE",
                "HTTPS_PROXY",
                "HTTP_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "https_proxy",
                "http_proxy",
                "all_proxy",
                "no_proxy",
            ]
            if name in os.environ
        }
        # No application keys, database URL, inherited provider accounts or personal Hermes home.
        env.update(
            {
                "HOME": directory,
                "USERPROFILE": directory,
                "HERMES_HOME": str(root / "home"),
                "PYTHONNOUSERSITE": "1",
                # Hermes prints Unicode even when its output is redirected on Windows.
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "SAM_HERMES_API_KEY": key.get_secret_value() if key else "",
            }
        )
        try:
            process = subprocess.Popen(
                [
                    str(settings.hermes_python.absolute()),
                    str(ROOT / "scripts/hermes_adapter.py"),
                    str(root),
                ],
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + settings.hermes_timeout_seconds
            output = root / "result.json"
            delivered = False
            try:
                while process.poll() is None:
                    if cancelled():
                        return None, "interrupted"
                    if time.monotonic() >= deadline:
                        return None, "timeout"
                    if output.is_file():
                        delivered = True
                        break
                    time.sleep(0.2)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            if not output.is_file():
                log.warning(
                    "hermes_runtime_diagnostic",
                    extra={"fields": {"phase": "process", "exit_code": process.returncode}},
                )
                return None, "runtime_process_error"
            if output.stat().st_size > 200000:
                return None, "invalid_report"
            result: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                return None, "invalid_report"
            if result.get("error_code"):
                code = result["error_code"]
                code = code if isinstance(code, str) and code in ERRORS else "runtime_error"
                detail = result.get("diagnostic", {})
                fields: dict[str, Any] = {"error_code": code, "exit_code": process.returncode}
                if isinstance(detail, dict):
                    for name, allowed in {
                        "phase": {
                            "input",
                            "imports",
                            "tools",
                            "initialization",
                            "conversation",
                            "report",
                        },
                        "exception_kind": {
                            "ModuleNotFoundError",
                            "ImportError",
                            "TypeError",
                            "ValueError",
                            "RuntimeError",
                            "OSError",
                            "UnicodeError",
                            "SystemExit",
                            "other",
                        },
                    }.items():
                        value = detail.get(name)
                        if isinstance(value, str) and value in allowed:
                            fields[name] = value
                log.warning("hermes_runtime_diagnostic", extra={"fields": fields})
                return None, code
            if process.returncode != 0 and not delivered:
                return None, "runtime_process_error"
            return result, None
        except (OSError, subprocess.SubprocessError):
            log.warning(
                "hermes_runtime_diagnostic",
                extra={"fields": {"phase": "process", "error_code": "runtime_process_error"}},
            )
            return None, "runtime_process_error"
        except ValueError:
            return None, "invalid_report"


Executor = Callable[
    [Settings, dict[str, Any], str, Callable[[], bool]], tuple[dict[str, Any] | None, str | None]
]


def work_once(
    engine: Engine, settings: Settings, stop: Event, execute: Executor = run_hermes
) -> bool:
    run_id = claim(engine, settings)
    if run_id is None:
        return False
    log.info("hermes_examination_started", extra={"fields": {"run_id": run_id}})
    with session_factory(engine)() as session:
        row = session.get_one(AgentReview, run_id)
        snapshot, model = row.snapshot, row.model

    def cancelled() -> bool:
        if stop.is_set():
            return True
        with session_factory(engine)() as session:
            row = session.get_one(AgentReview, run_id)
            return row.status != "running"

    try:
        result, error = execute(settings, snapshot, model, cancelled)
        finish(engine, run_id, result, error)
    except Exception as error:
        log.warning(
            "hermes_execution_failed",
            extra={"fields": {"run_id": run_id, "error_type": type(error).__name__}},
        )
        finish(engine, run_id, None, "runtime_error")
    return True


def worker(engine: Engine, settings: Settings, stop: Event, execute: Executor = run_hermes) -> None:
    log.info("hermes_worker_started")
    previous_code = None
    while not stop.is_set():
        try:
            code = configuration_status(settings)["code"]
            if code != previous_code:
                log.info("hermes_worker_configuration", extra={"fields": {"status": code}})
                previous_code = code
            if configured(settings) and work_once(engine, settings, stop, execute):
                continue
        except Exception as error:
            # Database may still need migration. Leave durable jobs for the next tick.
            log.warning(
                "hermes_worker_deferred", extra={"fields": {"error_type": type(error).__name__}}
            )
        stop.wait(2)
    log.info("hermes_worker_stopped")
