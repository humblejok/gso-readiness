"""Run one inspected validation command without a PowerShell pipeline; retain native evidence."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from revalidate_finding import source
from review_settings import process_environment


class ValidationRunError(ValueError):
    pass


def result_policy(command, requested="auto"):
    if requested not in {"auto", "native", "maven"}:
        raise ValidationRunError("Result policy must be auto, native or maven.")
    if requested != "auto":
        return requested
    name = command[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if command else ""
    return (
        "maven"
        if name
        in {
            "mvn",
            "mvn.cmd",
            "mvn.bat",
            "mvn.exe",
            "mvnw",
            "mvnw.cmd",
            "mvnw.bat",
        }
        else "native"
    )


def maven_result(paths):
    """Use Maven's complete summary lines, not the launcher's exit status."""
    outcomes = set()
    ansi = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
    summary = re.compile(rb"\s*(?:\[(?:INFO|ERROR)\]\s*)?BUILD (SUCCESS|FAILURE)\s*")
    for path in paths:
        with Path(path).open("rb") as handle:
            for line in handle:
                match = summary.fullmatch(ansi.sub(b"", line))
                if match:
                    outcomes.add(match[1])
    if outcomes == {b"SUCCESS"}:
        return "passed", "maven_build_success"
    if outcomes == {b"FAILURE"}:
        return "failed", "maven_build_failure"
    return (
        "unavailable",
        "maven_conflicting_summaries" if outcomes else "maven_summary_missing",
    )


def native_command(command, cwd, environment, windows=None):
    """Never interpret an agent-generated shell expression as a validation command."""
    windows = os.name == "nt" if windows is None else windows
    if not command or any(
        not arg or "\x00" in arg or "\n" in arg or "\r" in arg for arg in command
    ):
        raise ValidationRunError(
            "Supply one executable and individual nonempty arguments after --."
        )
    if any(
        re.search(
            r"(?i)^(?:--?D?|/d:)(?:[\w.-]*[._-])?(?:token|password|secret|login|authorization|api[-_.]?key)(?:=|:|$)",
            arg,
        )
        for arg in command[1:]
    ):
        raise ValidationRunError(
            "Do not put credentials in command arguments. Use the approved tool's credential store/environment."
        )
    executable = command[0]
    if Path(executable).is_absolute() or "/" in executable or "\\" in executable:
        candidate = Path(executable)
        resolved = str(candidate if candidate.is_absolute() else cwd / candidate)
        if not Path(resolved).is_file():
            raise ValidationRunError("The selected executable does not exist.")
    elif executable in {"mvnw", "mvnw.cmd", "gradlew", "gradlew.bat"}:
        resolved = str(cwd / executable)
        if not Path(resolved).is_file():
            raise ValidationRunError(
                "Project wrapper not found in the selected working directory. Specify the module directory or explicit wrapper path."
            )
    else:
        resolved = shutil.which(executable, path=environment.get("PATH"))
        if not resolved:
            raise ValidationRunError(
                "Executable not found. Check the approved tool installation/PATH; nothing was run."
            )
    # Maven/Gradle wrappers and Windows launchers are batch files, not PE executables.
    # cmd.exe is used only for this narrow compatibility boundary, never for pipes,
    # user-provided shell programs or AutoRun. Reject expansion/metacharacters.
    if windows and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
        arguments = [resolved, *command[1:]]
        if any(re.search(r'["%!&|<>^\r\n]', arg) for arg in arguments):
            raise ValidationRunError(
                "Unsafe batch-file argument/path. Use a native launcher or remove shell metacharacters; no quoting bypass was attempted."
            )
        comspec = (
            Path(environment.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
        )
        # Pass a raw Windows command line: list2cmdline on the /c payload would
        # introduce backslash-escaped quotes, which cmd.exe does not unescape.
        return (
            subprocess.list2cmdline([str(comspec), "/d", "/v:off", "/s", "/c"])
            + ' "'
            + " ".join('"' + arg + '"' for arg in arguments)
            + '"'
        )
    return [resolved, *command[1:]]


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                [
                    str(
                        Path(os.environ.get("SystemRoot", r"C:\Windows"))
                        / "System32"
                        / "taskkill.exe"
                    ),
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def execute(
    repository,
    command,
    *,
    cwd=None,
    output_root=None,
    timeout=900,
    max_output_bytes=64 * 1024 * 1024,
    policy="auto",
):
    policy = result_policy(command, policy)
    repository = Path(repository).resolve()
    cwd = Path(cwd).resolve() if cwd else repository
    if not cwd.is_dir() or not cwd.is_relative_to(repository):
        raise ValidationRunError(
            "Working directory must be inside the selected repository."
        )
    if not 1 <= timeout <= 3600 or not 1024 <= max_output_bytes <= 256 * 1024 * 1024:
        raise ValidationRunError(
            "Timeout/output limits are outside the supported bounds."
        )
    if output_root:
        output_root = Path(output_root).resolve()
        if output_root.is_relative_to(repository):
            raise ValidationRunError(
                "Store validation evidence outside the checkout, e.g. in the parent implementation attempt directory."
            )
        output_root.mkdir(parents=True, exist_ok=True)
    before = source(repository)
    directory = Path(tempfile.mkdtemp(prefix="graphify-check-", dir=output_root))
    receipt = {
        "schema_version": "1.0",
        "kind": "native-validation",
        "repository": str(repository),
        "cwd": str(cwd),
        "source_before": before,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "unavailable",
        "native_exit_code": None,
        "result_policy": policy,
        "exit_code_disagrees": None,
        "stdout": str(directory / "stdout.log"),
        "stderr": str(directory / "stderr.log"),
        "log_format": "raw bytes; decode explicitly for viewing; logs may contain private tool output",
        "receipt": str(directory / "result.json"),
    }
    process = None
    started = time.monotonic()
    try:
        environment = process_environment(repository)
        argv = native_command(command, cwd, environment)
        receipt["command"] = command
        receipt["launcher"] = (
            argv[0] if isinstance(argv, list) else "cmd.exe (batch launcher)"
        )
        with (
            (directory / "stdout.log").open("wb") as stdout,
            (directory / "stderr.log").open("wb") as stderr,
        ):
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                start_new_session=os.name != "nt",
            )
            while True:
                exceeded = (
                    sum(
                        (directory / name).stat().st_size
                        for name in ("stdout.log", "stderr.log")
                    )
                    > max_output_bytes
                )
                timed_out = time.monotonic() - started >= timeout
                if exceeded or timed_out:
                    receipt["reason"] = "output_limit" if exceeded else "timeout"
                    stop_process(process)
                    break
                if process.poll() is not None:
                    receipt["status"] = (
                        "passed" if process.returncode == 0 else "failed"
                    )
                    receipt["reason"] = "native_process_exit"
                    break
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            receipt["native_exit_code"] = process.returncode
        if policy == "maven" and receipt.get("reason") == "native_process_exit":
            receipt["status"], receipt["reason"] = maven_result(
                [directory / "stdout.log", directory / "stderr.log"]
            )
            if receipt["status"] in {"passed", "failed"}:
                receipt["exit_code_disagrees"] = (receipt["status"] == "passed") != (
                    process.returncode == 0
                )
        receipt["source_after"] = source(repository)
        receipt["source_unchanged"] = before == receipt["source_after"]
        if not receipt["source_unchanged"]:
            receipt["status"], receipt["reason"] = (
                "unavailable",
                "source_changed_during_check",
            )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        receipt["status"] = "unavailable"
        receipt["reason"] = (
            str(exc)
            if isinstance(exc, ValidationRunError)
            else "execution_or_source_capture_unavailable"
        )
    finally:
        if process and process.poll() is None:
            stop_process(process)
    receipt["duration_seconds"] = round(time.monotonic() - started, 3)
    with (directory / "result.json").open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--cwd")
    parser.add_argument("--output-root")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--result-policy",
        choices=("auto", "native", "maven"),
        default="auto",
        help="Auto trusts Maven build summaries for mvn/mvnw; other tools use their exit code.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        result = execute(
            args.repository,
            command,
            cwd=args.cwd,
            output_root=args.output_root,
            timeout=args.timeout,
            policy=args.result_policy,
        )
        # Do not echo raw command output or credential-bearing arguments to the chat terminal.
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "status",
                        "native_exit_code",
                        "result_policy",
                        "exit_code_disagrees",
                        "receipt",
                        "reason",
                    )
                },
                ensure_ascii=True,
            )
        )
        return {"passed": 0, "failed": 1, "unavailable": 2}[result["status"]]
    except (OSError, ValueError, subprocess.SubprocessError):
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "message": "Validation could not start. Check repository, executable and evidence directory; do not infer a build failure.",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
