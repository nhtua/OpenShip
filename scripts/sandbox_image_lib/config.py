"""Read only selected literal settings; never source/eval operator dotenv files."""

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path

from .builds import runtime_values
from .catalog import ImageError

MANAGED = {"standard", "logs-data", "iac-validation", "devops-shell"}
DEFAULT_PROFILES_FILE = "deploy/sandbox/sandbox-profiles.yaml"
KEYS = {
    "SANDBOX_ENABLED", "SANDBOX_PROFILE_ID", "SANDBOX_IMAGE", "SANDBOX_DEPENDENCY_IDENTITY",
    "OPENSHIP_SANDBOX_ENGINE_SOCKET", "XDG_RUNTIME_DIR",
}


def _literal(key, value):
    value = value.strip()
    if value.startswith(("'", '"')):
        match = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
        if not match:
            raise ImageError(f"{key} requires a single-line literal value or process override")
        value = match[2]
    else:
        value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
    if any(token in value for token in ("$", "`", "\n", "\r", "\\")):
        raise ImageError(f"{key} requires a literal value or process override (no interpolation)")
    return value


def _dotenv_value(root: Path, key: str) -> str | None:
    """Read one literal assignment from root .env without sourcing or evaluating it."""
    dotenv = root / ".env"
    try:
        if not dotenv.exists():
            return None
        lines = dotenv.read_text().splitlines()
    except OSError as exc:
        raise ImageError(f"unreadable {dotenv}") from exc
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Z_]+)\s*=(.*)$", line)
        if match and match[1] == key:
            return match[2]
    return None


def resolve_profiles_source(root: Path, environ: Mapping[str, str]) -> tuple[Path, bool]:
    """Resolve the host profile source: process env, root .env, then the default.

    Paths are always interpreted relative to the repository root, never the
    caller's working directory. The boolean reports an explicit override.
    """
    root = Path(root)
    if "SANDBOX_PROFILES_FILE" in environ:
        raw, explicit = environ["SANDBOX_PROFILES_FILE"], True
    else:
        raw = _dotenv_value(root, "SANDBOX_PROFILES_FILE")
        explicit = raw is not None
        if raw is None:
            raw = DEFAULT_PROFILES_FILE
    value = _literal("SANDBOX_PROFILES_FILE", raw)
    if not value:
        raise ImageError("SANDBOX_PROFILES_FILE requires a non-empty literal path")
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return Path(os.path.abspath(path)), explicit


def read_settings(root: Path, environ) -> dict[str, str]:
    raw = {}
    dotenv = root / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            match = re.match(r"^\s*(?:export\s+)?([A-Z_]+)\s*=(.*)$", line)
            if match and match[1] in KEYS:
                raw[match[1]] = match[2]
    raw.update({key: environ[key] for key in KEYS if key in environ})

    def selector(key, default):
        try:
            return _literal(key, raw.get(key, default)), True
        except ImageError:
            # Compose interpolation stays on the pre-existing path: this helper
            # cannot resolve it, so it must not tighten legacy/disabled startup.
            return raw.get(key, default).strip(), False

    enabled_value, enabled_literal = selector("SANDBOX_ENABLED", "false")
    profile_value, profile_literal = selector("SANDBOX_PROFILE_ID", "python311")
    result = {"SANDBOX_ENABLED": enabled_value, "SANDBOX_PROFILE_ID": profile_value}
    enabled = enabled_literal and enabled_value.lower() in ("1", "true", "yes", "on")
    strict = enabled and profile_literal and profile_value in MANAGED
    for key, value in raw.items():
        if key not in result:
            # Disabled/legacy normal starts must not validate unused new fields.
            result[key] = _literal(key, value) if strict else value
    return result


def resolve_endpoint(cli: str, settings) -> str:
    value = settings.get("OPENSHIP_SANDBOX_ENGINE_SOCKET", "")
    if not value:
        if cli == "docker":
            value = "/var/run/docker.sock"
        else:
            runtime = _literal("XDG_RUNTIME_DIR", settings.get("XDG_RUNTIME_DIR", ""))
            if not runtime:
                raise ImageError("set XDG_RUNTIME_DIR or OPENSHIP_SANDBOX_ENGINE_SOCKET for Podman")
            value = runtime + "/openship-sandbox/podman.sock"
    value = _literal("OPENSHIP_SANDBOX_ENGINE_SOCKET", value)
    path = value.removeprefix("unix://")
    if not path.startswith("/") or "://" in path:
        raise ImageError("OPENSHIP_SANDBOX_ENGINE_SOCKET must select an absolute Unix socket")
    try:
        valid = stat.S_ISSOCK(Path(path).stat().st_mode) and os.access(path, os.R_OK | os.W_OK)
    except OSError:
        valid = False
    if not valid:
        raise ImageError("OPENSHIP_SANDBOX_ENGINE_SOCKET is missing or inaccessible; select the serving daemon socket")
    return "unix://" + path


def validate_selection(recipe, settings) -> None:
    expected = runtime_values(recipe)
    for key, value in expected.items():
        configured = settings.get(key, "")
        if key == "SANDBOX_IMAGE":
            configured = configured.removeprefix("localhost/")
        if configured != value:
            assignments = "\n".join(f"{name}={setting}" for name, setting in expected.items())
            raise ImageError(f"{key} conflicts with the current managed recipe; select these literal settings:\n{assignments}")
