"""Nightly regression run — an authenticated caller of the shared Run API.

The nightly Databricks Job is **just another caller of the same Run API** the CI
Action and the GUI use (ADR-0005): there is no scheduled-only engine. Each fire
starts a run (``POST /runs``), polls ``GET /runs/{run_id}`` until the phase is
terminal — **owning its own timeout** — and maps the verdict **fail-closed** to a
process exit code (only ``pass`` → 0; ``fail`` / ``BLOCKED_BY_SAFETY`` / ``error``
/ ``timeout`` → non-zero). Nightly opts into ``depth=full`` (CI defaults to
``quick``); agentic Checks run advisory in a ``full`` run and never gate
(ADR-0007/0008).

Invoked as a ``spark_python_task`` with parameters, e.g.::

    --app-url https://<app-host>  --suite-id suite_ci_gate  --depth full
    --target-ids tgt_a,tgt_b      --timeout 1800

Auth uses the job's own Databricks identity (an OAuth token from the SDK) — no
raw credentials here. The token is a distinct, trigger+read identity, never the
override (ADR-0005 §6).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Ensure the backend package is importable whether this file is run from the
# repo root (bundle sync) or the databricks/jobs directory.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKEND = os.path.join(_REPO_ROOT, "backend")
for _p in (_BACKEND, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The fail-closed gate mapping (ADR-0005 §5): only PASS is a passing pipeline.
_PASS = "PASS"
_NON_ZERO_EXIT = 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Nightly regression suite runner (shared Run API caller)")
    p.add_argument("--app-url", default=os.environ.get("VALIDATION_APP_URL"),
                   help="Base URL of the deployed validation app (e.g. https://<app-host>).")
    p.add_argument("--suite-id", default=None)
    p.add_argument("--test-ids", default=None, help="Comma-separated Test ids.")
    p.add_argument("--target-ids", default=None, help="Comma-separated Target ids (else all).")
    p.add_argument("--depth", default="full", choices=["quick", "full"])
    p.add_argument("--timeout", type=int, default=1800, help="Caller-owned poll timeout (seconds).")
    p.add_argument("--poll-interval", type=int, default=5)
    return p.parse_args(argv)


def _token() -> str | None:
    """A bearer token for the job's own Databricks identity (no raw creds)."""
    try:
        from app.core.config import get_databricks_oauth_token

        return get_databricks_oauth_token()
    except Exception as exc:  # noqa: BLE001 - degrade cleanly if unavailable
        print(f"[run_suite] no OAuth token available ({exc}); calling without auth", flush=True)
        return None


def _request(url: str, *, method: str, token: str | None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - app-url is operator-supplied
        return json.loads(resp.read().decode())


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if not args.app_url:
        print("[run_suite] --app-url (or VALIDATION_APP_URL) is required", flush=True)
        return 2

    base = args.app_url.rstrip("/")
    token = _token()
    payload: dict = {"depth": args.depth, "request_source": "NIGHTLY", "requested_by": "nightly-dab"}
    if args.suite_id:
        payload["suite_ids"] = [args.suite_id]
    if args.test_ids:
        payload["test_ids"] = [s for s in args.test_ids.split(",") if s]
    if args.target_ids:
        payload["target_ids"] = [s for s in args.target_ids.split(",") if s]

    # Start the run.
    try:
        started = _request(f"{base}/api/v1/runs", method="POST", token=token, body=payload)
    except urllib.error.HTTPError as exc:  # a rejected submit is a hard, loud failure
        print(f"[run_suite] POST /runs failed: {exc.code} {exc.read().decode()[:400]}", flush=True)
        return _NON_ZERO_EXIT
    run_id = started.get("run_id") or started.get("id")
    print(f"[run_suite] started run {run_id} (depth={args.depth})", flush=True)

    # Poll until terminal — the caller owns the timeout (ADR-0005 §2).
    deadline = time.monotonic() + args.timeout
    while True:
        resource = _request(f"{base}/api/v1/runs/{run_id}", method="GET", token=token)
        phase = resource.get("phase")
        if phase == "terminal":
            verdict = resource.get("verdict")
            print(f"[run_suite] run {run_id} terminal: {verdict}", flush=True)
            # Fail-closed: only PASS passes; everything else blocks (ADR-0005 §5).
            return 0 if verdict == _PASS else _NON_ZERO_EXIT
        if time.monotonic() >= deadline:
            print(f"[run_suite] run {run_id} timed out in phase {phase!r} — fail-closed", flush=True)
            return _NON_ZERO_EXIT  # a caller-side timeout blocks the pipeline
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
