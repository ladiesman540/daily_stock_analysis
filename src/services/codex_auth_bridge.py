# -*- coding: utf-8 -*-
"""Local OpenAI Codex/ChatGPT auth bridge.

This module intentionally never logs or returns access-token values.  It reads
the official Codex auth file only on the backend, so the UI can opt into using
ChatGPT/Codex sign-in without copying the credential into browser state or .env.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_CODEX_AUTH_PATH = str(Path.home() / ".codex" / "auth.json")
DEFAULT_CODEX_CLI_PATH = "/Applications/Codex.app/Contents/Resources/codex"


def _resolve_path(value: Optional[str], default: str) -> str:
    raw = (value or "").strip() or default
    return str(Path(os.path.expanduser(raw)))


def resolve_codex_auth_path(value: Optional[str] = None) -> str:
    """Return the absolute Codex auth path."""
    return _resolve_path(value, DEFAULT_CODEX_AUTH_PATH)


def resolve_codex_cli_path(value: Optional[str] = None) -> str:
    """Return the Codex CLI path, preferring the desktop bundle when present."""
    raw = (value or "").strip()
    if raw:
        return _resolve_path(raw, DEFAULT_CODEX_CLI_PATH)
    bundled = Path(DEFAULT_CODEX_CLI_PATH)
    if bundled.exists():
        return str(bundled)
    return "codex"


def _read_auth_payload(auth_path: str) -> Dict[str, Any]:
    path = Path(auth_path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    parts = (token or "").split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1]
    padding = "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode((segment + padding).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_codex_login_status(cli_path: str) -> str:
    try:
        completed = subprocess.run(
            [cli_path, "login", "status"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
    except Exception as exc:
        return f"Unable to run Codex CLI: {exc}"

    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part and part.strip()
    )
    return output.strip()


def get_codex_auth_status(
    *,
    auth_path: Optional[str] = None,
    cli_path: Optional[str] = None,
    run_cli_status: bool = False,
) -> Dict[str, Any]:
    """Return safe Codex auth metadata without exposing tokens."""
    resolved_auth_path = resolve_codex_auth_path(auth_path)
    resolved_cli_path = resolve_codex_cli_path(cli_path)
    auth_payload = _read_auth_payload(resolved_auth_path)
    tokens = auth_payload.get("tokens") if isinstance(auth_payload.get("tokens"), dict) else {}
    access_token = str(tokens.get("access_token") or "")
    access_payload = _decode_jwt_payload(access_token)
    expires_at = int(access_payload.get("exp") or 0) if access_payload else 0
    now = int(time.time())
    token_present = bool(access_token)
    token_expired = bool(expires_at and expires_at <= now)

    status_message = ""
    if run_cli_status:
        status_message = _run_codex_login_status(resolved_cli_path)

    return {
        "auth_path": resolved_auth_path,
        "cli_path": resolved_cli_path,
        "auth_file_exists": Path(resolved_auth_path).exists(),
        "auth_mode": str(auth_payload.get("auth_mode") or ""),
        "account_id": str(tokens.get("account_id") or ""),
        "logged_in": token_present and not token_expired,
        "token_present": token_present,
        "token_expires_at": expires_at or None,
        "token_expired": token_expired,
        "status_message": status_message,
    }


def get_codex_access_token(
    *,
    auth_path: Optional[str] = None,
    cli_path: Optional[str] = None,
    min_ttl_seconds: int = 120,
) -> str:
    """Return a live Codex access token for backend-only model calls."""
    resolved_auth_path = resolve_codex_auth_path(auth_path)
    resolved_cli_path = resolve_codex_cli_path(cli_path)
    payload = _read_auth_payload(resolved_auth_path)
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    token = str(tokens.get("access_token") or "")
    if not token:
        return ""

    decoded = _decode_jwt_payload(token)
    expires_at = int(decoded.get("exp") or 0) if decoded else 0
    if expires_at and expires_at - int(time.time()) <= min_ttl_seconds:
        _run_codex_login_status(resolved_cli_path)
        payload = _read_auth_payload(resolved_auth_path)
        tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
        token = str(tokens.get("access_token") or "")
        decoded = _decode_jwt_payload(token)
        expires_at = int(decoded.get("exp") or 0) if decoded else 0

    if expires_at and expires_at <= int(time.time()):
        return ""
    return token


def _safe_tail(text: str, *, max_chars: int = 1200) -> str:
    """Return a short diagnostic tail without flooding logs."""
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[-max_chars:]


def run_codex_exec_text(
    prompt: str,
    *,
    model: str = "gpt-5.5",
    auth_path: Optional[str] = None,
    cli_path: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    reasoning_effort: str = "xhigh",
) -> str:
    """Generate one text response through the local Codex CLI.

    This is used only for backend final synthesis. It relies on the user's
    existing Codex/ChatGPT login and never exposes access tokens to the browser
    or stores them in app config.
    """
    resolved_auth_path = resolve_codex_auth_path(auth_path)
    resolved_cli_path = resolve_codex_cli_path(cli_path)
    if not Path(resolved_cli_path).exists() and "/" in resolved_cli_path:
        raise RuntimeError("Codex CLI not found at configured path")

    model_name = (model or "gpt-5.5").strip()
    if model_name.startswith("openai/"):
        model_name = model_name.split("/", 1)[1]

    tmp = tempfile.NamedTemporaryFile(
        prefix="dsa-codex-final-",
        suffix=".txt",
        dir="/private/tmp",
        delete=False,
    )
    output_path = tmp.name
    tmp.close()

    codex_home = str(Path(resolved_auth_path).parent)
    env = os.environ.copy()
    env["CODEX_HOME"] = codex_home
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "xterm-256color")

    args = [
        resolved_cli_path,
        "exec",
        "--model",
        model_name,
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-rules",
        "--ignore-user-config",
        "-c",
        'approval_policy="never"',
        "-c",
        f'model_reasoning_effort="{reasoning_effort or "xhigh"}"',
        "--output-last-message",
        output_path,
        "-",
    ]

    effective_timeout = max(30.0, float(timeout_seconds or 180.0))
    try:
        completed = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            check=False,
            cwd="/private/tmp",
            env=env,
        )
        content = ""
        try:
            content = Path(output_path).read_text(encoding="utf-8").strip()
        except OSError:
            content = ""

        if completed.returncode == 0 and content:
            return content
        if content:
            return content

        stdout_tail = _safe_tail(completed.stdout)
        stderr_tail = _safe_tail(completed.stderr)
        detail = "; ".join(part for part in (stdout_tail, stderr_tail) if part)
        if detail:
            raise RuntimeError(f"Codex CLI failed with exit code {completed.returncode}: {detail}")
        raise RuntimeError(f"Codex CLI failed with exit code {completed.returncode}")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Codex CLI timed out after {effective_timeout:.0f}s") from exc
    finally:
        try:
            Path(output_path).unlink(missing_ok=True)
        except OSError:
            pass
