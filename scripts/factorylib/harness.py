"""Bounded live CLI probes/review; production worker supervision stays with AO."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid

from .protocol import ProtocolError, usage


def command(route, role="worker", max_turns=12, max_cost=1.0):
    harness = route["harness"]
    if harness == "codex":
        argv = ["codex", "--ask-for-approval", "never", "exec", "--ephemeral", "--ignore-user-config",
                "--sandbox", "read-only" if role in ("review", "discovery") else "workspace-write", "--json", "-"]
        if route.get("model"):
            argv.extend(["--model", route["model"]])
        if route.get("effort"):
            argv.extend(["-c", "model_reasoning_effort=" + json.dumps(route["effort"])])
        return argv
    if harness == "claude-code":
        argv = ["claude", "--print", "--permission-mode", "default", "--no-session-persistence",
                "--output-format", "json", "--max-budget-usd", str(route.get("max_cost", max_cost)),
                "--max-turns", str(route.get("max_turns", max_turns)), "--allowedTools", "Read", "Glob", "Grep", "Skill"]
        if role == "worker":
            argv.extend(["Edit", "Write", "Bash(./scripts/verify)", "Bash(./scripts/factory checkpoint *)",
                         "Bash(./scripts/factory scope *)", "Bash(python3 -m unittest *)",
                         "Bash(./scripts/factory --help)", "Bash(python3 scripts/factory --help)",
                         "Bash(python3 scripts/factory checkpoint *)", "Bash(python3 scripts/factory scope *)",
                         "Bash(git status *)", "Bash(git diff *)", "Bash(git add *)", "Bash(git commit *)"])
        elif role == "review":
            argv.extend(["Bash(git diff *)", "Bash(git show *)", "Bash(git status *)"])
        if route.get("model"):
            argv.extend(["--model", route["model"]])
        if route.get("effort"):
            argv.extend(["--effort", route["effort"]])
        return argv
    raise ProtocolError(f"live CLI {harness} is not probed; use AO adapter after installing and checking capabilities")


def run(root, route, prompt, role="worker", seconds=180):
    run_id = uuid.uuid4().hex
    directory = Path(root) / ".factory-runtime" / "live"
    directory.mkdir(parents=True, exist_ok=True)
    stdout_path, stderr_path = directory / f"{run_id}.jsonl", directory / f"{run_id}.stderr"
    argv = command(route, role)
    started = time.monotonic()
    timed_out = False
    # The prompt is stdin, not shell code or a loggable command-line argument.
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(argv, cwd=root, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
        except OSError as exc:
            return {"run_id": run_id, "harness": route["harness"], "role": role,
                    "passed": False, "returncode": 127, "timed_out": False,
                    "provider_errors": [type(exc).__name__], "duration_seconds": 0,
                    "context_bytes": len(prompt.encode()), "usage": usage([], route["harness"]),
                    "final": "", "events_path": str(stdout_path)}
        try:
            process.communicate(prompt.encode(), timeout=seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    events, final = [], ""
    for line in stdout_path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
        if event.get("type") == "result":
            final = event.get("result", "")
        item = event.get("item", {})
        if item.get("type") == "agent_message":
            final = item.get("text", "")
    provider_errors = [e.get("error") for e in events if e.get("type") in ("error", "turn.failed")]
    provider_errors += [e.get("subtype") for e in events if e.get("type") == "result" and e.get("subtype") != "success"]
    denials = [d.get("tool_name") for e in events for d in e.get("permission_denials", [])]
    return {"run_id": run_id, "harness": route["harness"], "role": role,
            "returncode": process.returncode, "timed_out": timed_out, "provider_errors": provider_errors,
            "permission_denials": denials,
            "passed": process.returncode == 0 and not timed_out and not provider_errors and bool(final),
            "duration_seconds": round(time.monotonic() - started, 3), "context_bytes": len(prompt.encode()),
            "usage": usage(events, route["harness"]), "final": final, "events_path": str(stdout_path)}


def review_json(text):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("reviewer did not return a single JSON object") from exc
    if not isinstance(value, dict) or value.get("verdict") not in ("approved", "changes_requested") or not isinstance(value.get("findings"), list) or any(not isinstance(item, str) for item in value["findings"]):
        raise ProtocolError("invalid reviewer response")
    return value
