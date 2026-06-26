#!/usr/bin/env python3
"""Entrypoint for running the app on Databricks Apps (and locally).

Adds the backend dir to the path, installs requirements when present, and
starts uvicorn bound to 0.0.0.0 on the Databricks-provided port. The port is
read from the environment - never hardcoded - so it adapts to the platform.
"""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> None:
    # Resolve the python executable to an ABSOLUTE path BEFORE chdir. On
    # Databricks Apps sys.executable is a relative path (".venv/bin/python"),
    # which breaks once we change directories - so capture it up front.
    python_exe = os.path.abspath(sys.executable)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    print(f"[run] cwd={os.getcwd()} python={python_exe}", flush=True)

    # Install Python deps at startup. The Databricks Apps buildpack only
    # auto-installs a requirements.txt located at the source-code ROOT; ours
    # lives in backend/, so we install it here (matches the proven app pattern).
    # Skippable locally (deps already in the venv) via INSTALL_REQUIREMENTS=0.
    req = os.path.join(script_dir, "requirements.txt")
    if os.path.exists(req) and os.environ.get("INSTALL_REQUIREMENTS", "1") == "1":
        print(f"[run] installing requirements from {req}", flush=True)
        subprocess.run([python_exe, "-m", "pip", "install", "-r", req, "-q"], check=True)

    port = int(
        os.environ.get("DATABRICKS_APP_PORT")
        or os.environ.get("PORT")
        or "8000"
    )

    import uvicorn

    from app.main import app

    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
