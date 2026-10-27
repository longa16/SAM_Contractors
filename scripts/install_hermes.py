"""Install the reviewed Hermes revision in a dedicated runtime, without touching the app venv."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "11a75355a4c5540154597c7ea23cbc2a933539f5"
REPOSITORY = "https://github.com/NousResearch/hermes-agent.git"


def main() -> None:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Installez uv avant d’installer Hermes.")
    runtime = ROOT / ".runtime"
    source = runtime / "hermes-source"
    source.mkdir(parents=True, exist_ok=True)
    if not (source / ".git").exists():
        subprocess.run(["git", "init", str(source)], check=True)
        subprocess.run(
            ["git", "-C", str(source), "remote", "add", "origin", REPOSITORY], check=True
        )
    remote = subprocess.check_output(
        ["git", "-C", str(source), "remote", "get-url", "origin"], text=True
    ).strip()
    if remote != REPOSITORY:
        raise SystemExit("Le répertoire runtime existant pointe vers un autre dépôt.")
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)
    if dirty:
        raise SystemExit(
            "Le runtime contient des modifications locales ; installation interrompue."
        )
    subprocess.run(
        ["git", "-C", str(source), "fetch", "--depth", "1", "origin", REVISION], check=True
    )
    subprocess.run(["git", "-C", str(source), "checkout", "--detach", REVISION], check=True)
    environment = os.environ.copy()
    environment.setdefault("UV_CACHE_DIR", str(runtime / "cache"))
    environment.setdefault("UV_PYTHON_INSTALL_DIR", str(runtime / "python"))
    environment["UV_PROJECT_ENVIRONMENT"] = str(runtime / "hermes-venv")
    subprocess.run(
        [uv, "sync", "--project", str(source), "--frozen", "--no-dev", "--python", "3.14"],
        env=environment,
        check=True,
    )
    print("Hermes installé à la révision " + REVISION)
    print("Ajoutez HERMES_ENABLED=true dans .env, puis redémarrez l’application.")
    if os.name == "nt":
        print("HERMES_PYTHON=" + str(runtime / "hermes-venv/Scripts/python.exe"))


if __name__ == "__main__":
    main()
