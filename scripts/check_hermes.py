"""Local, offline configuration diagnosis. Never prints credentials or calls a model."""

import json
import os
import subprocess

from app.config import Settings
from app.services.agent_reviews import configuration_status


def main() -> None:
    settings = Settings()
    result = configuration_status(settings)
    print(result["message"])
    print("Python Hermes : " + str(settings.hermes_python))
    print("Modèle : " + settings.hermes_model)
    if not result["ready"]:
        raise SystemExit(1)
    # -I ignores inherited Python paths and user site packages. No provider keys are needed.
    env = {name: os.environ[name] for name in ["PATH", "SYSTEMROOT"] if name in os.environ}
    try:
        probe = subprocess.run(
            [
                str(settings.hermes_python.absolute()),
                "-I",
                "-c",
                "import importlib.util,json; "
                "print(json.dumps({'installed': "
                "importlib.util.find_spec('run_agent') is not None}))",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        installed = json.loads(probe.stdout).get("installed")
    except (OSError, ValueError, subprocess.SubprocessError):
        installed = False
    if not installed:
        print(
            "Ce Python ne contient pas Hermes Agent. "
            "Lancez uv run python -m scripts.install_hermes et vérifiez HERMES_PYTHON."
        )
        raise SystemExit(1)
    print(
        "Runtime Hermes présent. Redémarrez le serveur, ouvrez une candidature "
        "à l’étape Examen en cours, puis cliquez sur Lancer l’examen avec Hermes."
    )
    print("Ce contrôle n’appelle pas le fournisseur LLM et ne vérifie pas son quota.")


if __name__ == "__main__":
    main()
