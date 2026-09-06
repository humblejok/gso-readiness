#!/usr/bin/env python3
"""Create or validate the review kit's private Python environment.

This script intentionally uses only the standard library so it can run before the
virtual environment exists. It never replaces a directory that is not recognizably
a Python virtual environment.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from review_core import load_yaml

KIT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = KIT_ROOT.parents[1]
DEFAULT_POLICY = KIT_ROOT / "policy.yaml"
DEFAULT_VENV_NAME = ".graphify-review-venv"


class BootstrapError(RuntimeError):
    """Raised when environment setup cannot proceed safely."""


def workspace_path(workspace: Path, raw_path: str, label: str) -> Path:
    candidate = (workspace / raw_path).resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError as exc:
        raise BootstrapError(f"{label} must remain inside the workspace: {raw_path}") from exc
    return candidate


def required_graphify_version(requirements: Path) -> str:
    if not requirements.is_file():
        raise BootstrapError(f"requirements file does not exist: {requirements}")
    pattern = re.compile(r"^\s*graphifyy\s*==\s*([A-Za-z0-9_.+-]+)\s*(?:#.*)?$", re.IGNORECASE)
    for line in requirements.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1)
    raise BootstrapError(f"{requirements} must pin graphifyy with ==")


def parse_minimum_python(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)", value)
    if not match:
        raise BootstrapError("runtime.minimum_python must use major.minor format, for example 3.10")
    return int(match.group(1)), int(match.group(2))


def environment_commands(venv: Path) -> tuple[Path, Path]:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe", venv / "Scripts" / "graphify.exe"
    return venv / "bin" / "python", venv / "bin" / "graphify"


def inspect_environment(venv: Path, expected_version: str, minimum_python: tuple[int, int]) -> dict[str, Any]:
    python_path, graphify_path = environment_commands(venv)
    result: dict[str, Any] = {
        "exists": venv.exists(),
        "is_virtual_environment": (venv / "pyvenv.cfg").is_file(),
        "python": str(python_path),
        "graphify": str(graphify_path),
        "expected_graphify_version": expected_version,
        "ready": False,
    }
    if not result["exists"] or not result["is_virtual_environment"] or not python_path.is_file():
        return result
    probe = (
        "import importlib.metadata,json,sys;"
        "\ntry:\n v=importlib.metadata.version('graphifyy')"
        "\nexcept importlib.metadata.PackageNotFoundError:\n v=None"
        "\nprint(json.dumps({'python_version':[sys.version_info.major,sys.version_info.minor,sys.version_info.micro],'graphifyy':v}))"
    )
    try:
        completed = subprocess.run([str(python_path), "-c", probe], check=True, capture_output=True, text=True)
        details = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        result["probe_error"] = str(exc)
        return result
    result.update(details)
    result["ready"] = (
        tuple(details.get("python_version", [0, 0])[:2]) >= minimum_python
        and details.get("graphifyy") == expected_version
        and graphify_path.is_file()
    )
    return result


def bootstrap(
    workspace: Path,
    policy: dict[str, Any],
    *,
    check_only: bool = False,
    offline: bool = False,
) -> dict[str, Any]:
    runtime = policy.get("runtime", {})
    venv = workspace_path(workspace, str(runtime.get("venv_path", DEFAULT_VENV_NAME)), "runtime.venv_path")
    requirements = workspace_path(
        workspace,
        str(runtime.get("requirements_file", ".github/graphify-review/requirements.txt")),
        "runtime.requirements_file",
    )
    minimum = parse_minimum_python(str(runtime.get("minimum_python", "3.10")))
    required_version = required_graphify_version(requirements)
    state = inspect_environment(venv, required_version, minimum)
    if state["ready"]:
        state["action"] = "none"
        return state
    if check_only:
        state["action"] = "required"
        return state
    if venv.exists() and not state["is_virtual_environment"]:
        raise BootstrapError(
            f"refusing to modify {venv}: it exists but has no pyvenv.cfg; move it or change runtime.venv_path"
        )
    if sys.version_info[:2] < minimum:
        raise BootstrapError(
            f"bootstrap Python {sys.version_info.major}.{sys.version_info.minor} is below required {minimum[0]}.{minimum[1]}"
        )
    created = not venv.exists()
    if created:
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python_path, _ = environment_commands(venv)
    if not python_path.is_file():
        raise BootstrapError(f"virtual environment Python was not created: {python_path}")
    install = [
        str(python_path), "-m", "pip", "install", "--disable-pip-version-check",
        "--requirement", str(requirements),
    ]
    if offline:
        install.insert(4, "--no-index")
    try:
        subprocess.run(install, check=True)
    except subprocess.CalledProcessError as exc:
        hint = " The environment was created but installation failed; rerun when package access is available."
        raise BootstrapError(f"dependency installation failed with exit code {exc.returncode}.{hint}") from exc
    state = inspect_environment(venv, required_version, minimum)
    if not state["ready"]:
        raise BootstrapError("installation completed but Graphify did not pass the environment probe")
    state["action"] = "created" if created else "updated"
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--workspace", default=str(WORKSPACE_ROOT))
    parser.add_argument("--check", action="store_true", help="report status without creating or installing")
    parser.add_argument("--first-call", action="store_true", help="honor runtime.auto_bootstrap; used by the Copilot manager")
    parser.add_argument("--offline", action="store_true", help="use only packages already available to pip locally")
    parser.add_argument("--json", action="store_true", help="emit machine-readable status")
    args = parser.parse_args()
    try:
        policy = load_yaml(args.policy)
        check_only = args.check or (args.first_call and not bool(policy.get("runtime", {}).get("auto_bootstrap")))
        state = bootstrap(Path(args.workspace).resolve(), policy, check_only=check_only, offline=args.offline)
    except (BootstrapError, subprocess.CalledProcessError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(state, indent=2))
    else:
        label = "ready" if state["ready"] else "setup required"
        print(f"Review environment {label}: {state.get('action', 'none')}")
        print(f"Python: {state['python']}")
        print(f"Python version: {'.'.join(str(item) for item in state.get('python_version', [])) or 'unknown'}")
        print(f"Graphify: {state['graphify']}")
        print(f"Graphify version: {state.get('graphifyy') or 'not installed'} (required {state['expected_graphify_version']})")
    return 0 if state["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
