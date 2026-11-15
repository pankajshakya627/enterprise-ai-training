"""Exercise Make service lifecycle and prerequisites without external integrations."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest

Workspace = tuple[Path, list[str], dict[str, str]]
STUB = """#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path

def record(event, **values):
    with open(os.environ["MAKE_LOG"], "a") as stream:
        stream.write(json.dumps({"event": event, **values}) + "\\n")

if Path(sys.argv[0]).name == "curl":
    url = sys.argv[-1]
    record("health", url=url)
    failing = os.environ.get("FAIL_HEALTH")
    sys.exit(1 if failing and failing in url else 0)
if sys.argv[1] == "sync":
    record("sync", args=sys.argv[1:])
    sys.exit(0)
if Path(sys.argv[0]).name == "npx":
    record("inspector", args=sys.argv[1:])
    sys.exit(0)
if "-c" in sys.argv:
    record("prereqs", args=sys.argv[1:])
    if os.environ.get("FAIL_CONFIG"):
        sys.stderr.write("private-configuration-value")
        sys.exit(1)
    sys.exit(0)
if "-m" in sys.argv:
    module = sys.argv[sys.argv.index("-m") + 1]
    if module.startswith(("app.mcp.", "app.evaluation.")) and module != "app.mcp.banking_server":
        record("cli", module=module, args=sys.argv[1:])
        if module == "app.mcp.server":
            sys.stdout.write("protocol-output")
        sys.exit(0)
if "app.mcp.banking_server" in sys.argv:
    port = "9002"
else:
    flag = "--port" if "--port" in sys.argv else "--server.port"
    port = sys.argv[sys.argv.index(flag) + 1]
def stop(signum, frame):
    record("stop", port=port)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
record("start", port=port, pid=os.getpid(), args=sys.argv[1:])
if port == os.environ.get("FAIL_PORT"):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rows = [json.loads(line) for line in Path(os.environ["MAKE_LOG"]).read_text().splitlines()]
        if sum(row["event"] == "start" for row in rows) == 4:
            sys.exit(int(os.environ["FAIL_EXIT"]))
        time.sleep(0.02)
    sys.exit(9)
while True:
    signal.pause()
"""


@pytest.fixture
def make_workspace(tmp_path: Path) -> Workspace:
    """Copy Make into a space-containing directory and replace external commands.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        Project directory, Make command and isolated child environment.
    """
    root = tmp_path / "project with spaces"
    root.mkdir()
    (root / "Makefile").write_bytes((Path(__file__).parents[1] / "Makefile").read_bytes())
    (root / ".env.example").write_text("EXAMPLE=synthetic\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    for name in ("uv", "curl", "npx"):
        path = binary / name
        path.write_text(STUB)
        path.chmod(0o755)
    command = ["make", f"UV={binary / 'uv'}", "ENV_FILE=runtime settings.env"]
    environment = {
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "MAKE_LOG": str(root / "events.jsonl"),
    }
    return root, command, environment


def _events(root: Path) -> list[dict[str, Any]]:
    path = root / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@contextmanager
def _stack(
    workspace: Workspace, target: str = "run", **extra: str
) -> Iterator[subprocess.Popen[str]]:
    root, command, environment = workspace
    (root / "runtime settings.env").write_text("CONFIGURED=synthetic\n")
    process = subprocess.Popen(
        [*command, target],
        cwd=root,
        env={**environment, **extra},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        yield process
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)


def test_setup_creates_template_and_preserves_existing_configuration(
    make_workspace: Workspace,
) -> None:
    """Setup creates missing configuration and retains an existing file.

    Args:
        make_workspace: Makefile with isolated dependency commands.
    """
    root, command, environment = make_workspace
    subprocess.run([*command, "setup"], cwd=root, env=environment, check=True, capture_output=True)
    config = root / "runtime settings.env"
    assert config.read_text() == (root / ".env.example").read_text()
    config.write_text("EXISTING=keep-this-value\n")
    subprocess.run([*command, "setup"], cwd=root, env=environment, check=True, capture_output=True)
    assert config.read_text() == "EXISTING=keep-this-value\n"
    assert all("--locked" in row["args"] for row in _events(root))


@pytest.mark.parametrize("failure", ["missing_file", "invalid_settings"])
def test_prerequisite_failure_starts_nothing_and_hides_values(
    make_workspace: Workspace, failure: str
) -> None:
    """Failed prerequisites prevent launches and avoid exposing configuration values.

    Args:
        make_workspace: Makefile with isolated dependency commands.
        failure: Missing dotenv or invalid model configuration.
    """
    root, command, environment = make_workspace
    if failure == "invalid_settings":
        (root / "runtime settings.env").write_text("CONFIGURED=synthetic\n")
        environment["FAIL_CONFIG"] = "1"
    result = subprocess.run(
        [*command, "run"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0
    assert "make setup" in result.stdout
    assert "private-configuration-value" not in result.stdout + result.stderr
    assert not any(row["event"] == "start" for row in _events(root))


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_stack_signal_stops_only_its_services(make_workspace: Workspace, signum: int) -> None:
    """Interrupting the stack cleans up its four services and preserves other processes.

    Args:
        make_workspace: Makefile with isolated dependency commands.
        signum: Terminal interrupt or termination signal.
    """
    root, _, _ = make_workspace
    with subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    ) as unrelated:
        try:
            with _stack(make_workspace) as process:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    started = [row for row in _events(root) if row["event"] == "start"]
                    if len(started) == 4:
                        break
                    time.sleep(0.02)
                assert {row["port"] for row in started} == {"8000", "8080", "8001", "8501"}
                os.killpg(process.pid, signum)
                process.communicate(timeout=8)
                assert {row["port"] for row in _events(root) if row["event"] == "stop"} == {
                    "8000",
                    "8080",
                    "8001",
                    "8501",
                }
                for row in started:
                    with pytest.raises(ProcessLookupError):
                        os.kill(row["pid"], 0)
                assert unrelated.poll() is None
        finally:
            unrelated.terminate()


@pytest.mark.parametrize("exit_status", [0, 7])
def test_exited_service_fails_stack_and_stops_siblings(
    make_workspace: Workspace, exit_status: int
) -> None:
    """A stopped required service makes the stack fail and cleans up siblings.

    Args:
        make_workspace: Makefile with isolated dependency commands.
        exit_status: Clean stop or crash of the synthetic banking process.
    """
    root, _, _ = make_workspace
    with _stack(make_workspace, FAIL_PORT="8080", FAIL_EXIT=str(exit_status)) as process:
        output, _ = process.communicate(timeout=8)
        assert process.returncode != 0
        assert "stopping the stack" in output
        started = [row for row in _events(root) if row["event"] == "start"]
        assert len(started) == 4
        assert {row["port"] for row in _events(root) if row["event"] == "stop"} == {
            "8000",
            "8001",
            "8501",
        }
        for row in started:
            with pytest.raises(ProcessLookupError):
                os.kill(row["pid"], 0)


@pytest.mark.parametrize("failed_endpoint", ["", "8080/openapi.json"])
def test_health_checks_every_service_and_reports_failure(
    make_workspace: Workspace, failed_endpoint: str
) -> None:
    """Health reports failure while still checking all four services.

    Args:
        make_workspace: Makefile with isolated HTTP commands.
        failed_endpoint: Endpoint to mark unavailable, or an empty value for success.
    """
    root, command, environment = make_workspace
    result = subprocess.run(
        [*command, "health"],
        cwd=root,
        env={**environment, "FAIL_HEALTH": failed_endpoint},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert (result.returncode == 0) == (not failed_endpoint)
    assert len(_events(root)) == 4
    assert ("DOWN" in result.stdout) == bool(failed_endpoint)


@pytest.mark.parametrize("target", ["run-mcp", "mcp-demo", "mcp-inspect"])
def test_cli_targets_forward_custom_environment(make_workspace: Workspace, target: str) -> None:
    root, command, environment = make_workspace
    (root / "runtime settings.env").write_text("CONFIGURED=synthetic\n")
    baseline = root / "reviewed baseline.json"
    baseline.write_text("{}")
    result = subprocess.run(
        [*command, target, f"AI_BASELINE={baseline}", "MCP_OUTPUT=demo output.json"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    rows = _events(root)
    assert rows
    args = rows[0]["args"]
    assert args[args.index("--env-file") + 1] == "runtime settings.env"
    if target == "run-mcp":
        assert result.stdout == "protocol-output"
    elif target == "mcp-demo":
        assert args[args.index("--output") + 1] == "demo output.json"
    elif target == "mcp-inspect":
        assert rows[0]["event"] == "inspector"
        assert "--" in args and "app.mcp.server" in args


def test_mcp_launch_without_configuration_starts_nothing(make_workspace: Workspace) -> None:
    root, command, environment = make_workspace
    result = subprocess.run(
        [*command, "run-mcp"],
        cwd=root,
        env=environment,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0
    assert not _events(root)


def test_http_stack_launches_and_cleans_all_five_services(make_workspace: Workspace) -> None:
    root, _, _ = make_workspace
    with _stack(make_workspace, target="run-http") as process:
        deadline = time.monotonic() + 5
        started = []
        while time.monotonic() < deadline:
            started = [row for row in _events(root) if row["event"] == "start"]
            if len(started) == 5:
                break
            time.sleep(0.02)
        assert {row["port"] for row in started} == {"8000", "8080", "8001", "8501", "9002"}
        for row in started:
            assert "BANKING_MCP_TRANSPORT=http" in row["args"]
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=8)
        assert len([row for row in _events(root) if row["event"] == "stop"]) == 5


def test_banking_http_target_uses_custom_environment(make_workspace: Workspace) -> None:
    root, command, environment = make_workspace
    (root / "runtime settings.env").write_text("CONFIGURED=synthetic\n")
    result = subprocess.run(
        [*command, "-n", "run-banking-mcp"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"runtime settings.env"' in result.stdout
    assert "BANKING_MCP_TRANSPORT=http" in result.stdout
    assert "app.mcp.banking_server" in result.stdout


def test_hosted_stack_starts_only_rag_agent_and_ui(make_workspace: Workspace) -> None:
    root, _, _ = make_workspace
    with _stack(make_workspace, target="run-hosted") as process:
        deadline = time.monotonic() + 5
        started = []
        while time.monotonic() < deadline:
            started = [row for row in _events(root) if row["event"] == "start"]
            if len(started) == 3 or process.poll() is not None:
                break
            time.sleep(0.02)
        assert {row["port"] for row in started} == {"8000", "8001", "8501"}
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=8)
        assert {row["port"] for row in _events(root) if row["event"] == "stop"} == {
            "8000",
            "8001",
            "8501",
        }
