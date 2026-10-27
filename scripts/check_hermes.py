"""Local, offline configuration diagnosis. Never prints credentials or calls a model."""

import json
import os
import subprocess
import tempfile

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
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    try:
        with tempfile.TemporaryDirectory(prefix="sam-hermes-check-") as home:
            env.update({"HOME": home, "USERPROFILE": home, "HERMES_HOME": home})
            probe = subprocess.run(
                [
                    str(settings.hermes_python.absolute()),
                    "-I",
                    "-c",
                    "import inspect,json; from run_agent import AIAgent; "
                    "from tools.registry import registry; "
                    "from toolsets import create_custom_toolset; "
                    "required={'model','enabled_toolsets','skip_memory','skip_background_review',"
                    "'run_budget_seconds','load_soul_identity'}; "
                    "print(json.dumps({'installed': "
                    "required.issubset(inspect.signature(AIAgent).parameters)}))",
                ],
                cwd=home,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=True,
            )
        installed = json.loads(probe.stdout.strip().splitlines()[-1]).get("installed")
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        installed = False
    if not installed:
        print(
            "Ce Python ne peut pas charger la version attendue de Hermes Agent et ses dépendances. "
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
