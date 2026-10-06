"""Git protocol and deterministic gates. Python standard library only."""
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import tempfile
import time


class ProtocolError(ValueError):
    pass


TASK_ID = re.compile(r"TASK-[0-9]{3,}")
STATES = {"READY", "RUNNING", "VERIFYING", "REVIEWING", "ACCEPTED", "PR_OPEN",
          "MERGED", "DIAGNOSING", "NEEDS_HUMAN"}
# Capability truth is per transport, not a blanket provider claim.
CAPABILITIES = {
    "codex": {"ao": {"effort"}, "cli": {"effort", "usage", "ephemeral", "sandbox", "resume"}},
    "claude-code": {"ao": {"effort"}, "cli": {"effort", "max_cost", "max_turns", "usage", "ephemeral", "resume"}},
    "cursor": {"ao": set(), "cli": set()},
    "grok": {"ao": set(), "cli": set()},
}


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProtocolError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(Path(path).read_text(), object_pairs_hook=unique,
                          parse_constant=lambda x: (_ for _ in ()).throw(ProtocolError(f"invalid number {x}")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot read {path}: {exc}") from exc


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def relative(root, name, *, exists=False, glob=False):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ProtocolError(f"invalid repository path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("..", ".git") for part in path.parts):
        raise ProtocolError(f"unsafe repository path: {name}")
    if not glob and any(c in name for c in "*?["):
        raise ProtocolError(f"expected concrete file: {name}")
    candidate = Path(root) / name
    if not candidate.resolve().is_relative_to(Path(root).resolve()):
        raise ProtocolError(f"path escapes repository: {name}")
    if exists and not candidate.is_file():
        raise ProtocolError(f"missing file: {name}")
    return candidate


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{label} must be a nonempty string")


def argv_list(value, label):
    if not isinstance(value, list) or not value:
        raise ProtocolError(f"{label} must contain command argv arrays")
    for command in value:
        if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x or "\x00" in x for x in command):
            raise ProtocolError(f"{label} requires argv arrays, never shell strings")


def task_path(root, task_id):
    if not TASK_ID.fullmatch(task_id):
        raise ProtocolError("task id must be TASK- followed by at least three digits")
    return relative(root, f".project/tasks/{task_id}.json")


def load_task(root, task_id):
    task = read_json(task_path(root, task_id))
    required = {"id", "title", "goal", "acceptance", "scope", "verification"}
    optional = {"references", "skills", "depends_on", "constraints", "risk", "milestone"}
    if not isinstance(task, dict) or required - task.keys() or task.keys() - required - optional:
        raise ProtocolError(f"{task_id}: missing or unknown contract fields")
    if task["id"] != task_id:
        raise ProtocolError("filename and task id differ")
    for key in ("title", "goal"):
        nonempty(task[key], key)
    for key in ("acceptance", "scope", "references", "skills", "depends_on", "constraints"):
        value = task.get(key, [])
        if not isinstance(value, list) or len(set(str(x) for x in value)) != len(value):
            raise ProtocolError(f"{key} must be a unique list")
        for item in value:
            nonempty(item, key)
        if key in ("scope", "acceptance") and not value:
            raise ProtocolError(f"{key} cannot be empty")
    for name in task["scope"]:
        relative(root, name, glob=True)
        if name.startswith((".git", ".project/", ".agents/", ".claude/", ".cursor/", ".github/")) or name in ("AGENTS.md", "CLAUDE.md"):
            raise ProtocolError("implementation scope cannot include protected protocol files")
    for name in task.get("references", []):
        relative(root, name, exists=True)
    for skill in task.get("skills", []):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", skill):
            raise ProtocolError("invalid skill name")
        relative(root, f".agents/skills/{skill}/SKILL.md", exists=True)
    for dependency in task.get("depends_on", []):
        if dependency == task_id or not TASK_ID.fullmatch(dependency):
            raise ProtocolError("invalid dependency")
    if task.get("risk", "standard") not in ("cheap", "standard", "strong"):
        raise ProtocolError("risk must be cheap, standard or strong")
    argv_list(task["verification"], "verification")
    return task


def load_policy(root):
    policy = read_json(relative(root, ".project/policy.json", exists=True))
    if policy.get("version") != 1:
        raise ProtocolError("unsupported policy version")
    for key in ("cheap", "standard", "strong", "review"):
        route = policy["routing"][key]
        if route.get("harness") not in CAPABILITIES:
            raise ProtocolError(f"unknown harness in route {key}")
        if route.keys() - {"harness", "model", "effort", "max_cost", "max_turns", "max_tokens"}:
            raise ProtocolError(f"unknown route controls: {key}")
        for budget in ("max_cost", "max_turns", "max_tokens"):
            if budget in route and (type(route[budget]) not in (float, int) or route[budget] <= 0):
                raise ProtocolError(f"invalid {budget}")
    for key in ("context_bytes", "attempts", "diagnostics", "task_seconds", "verify_seconds"):
        value = policy["limits"].get(key)
        if type(value) is not int or value < (0 if key == "diagnostics" else 1):
            raise ProtocolError(f"invalid limit {key}")
    if policy["limits"]["context_bytes"] > 16000:
        raise ProtocolError("context cap exceeds AO prompt field bound")
    if policy["ao"].get("permission") not in ("default", "accept-edits", "auto"):
        raise ProtocolError("explicit non-bypass permission required")
    if policy["ao"].get("mode") not in ("chat", "tui"):
        raise ProtocolError("unknown AO mode")
    if type(policy.get("publish")) is not bool or type(policy.get("auto_merge")) is not bool:
        raise ProtocolError("publish/auto_merge must be explicit booleans")
    argv_list(policy["verification"], "canonical verification")
    if policy.get("verification_isolation", "bubblewrap") not in ("bubblewrap", "trusted-local"):
        raise ProtocolError("verification isolation must be bubblewrap or explicit trusted-local test mode")
    for key, value in policy.get("observed_budget", {}).items():
        if key not in ("input_tokens", "estimated_cost_usd") or type(value) not in (int, float) or value <= 0:
            raise ProtocolError("invalid observed budget")
    return policy


def route_task(policy, task, override=None, transport="ao", review=False):
    route = dict(policy["routing"]["review" if review else task.get("risk", "standard")])
    if override:
        # Preserve hard budgets, but do not inherit another provider's model/effort.
        route = {k: v for k, v in route.items() if k in ("max_cost", "max_turns", "max_tokens")}
        route["harness"] = override
    harness = route["harness"]
    if harness not in CAPABILITIES:
        raise ProtocolError(f"unknown provider: {harness}")
    if transport == "ao" and harness == "codex" and policy["ao"]["permission"] == "default":
        raise ProtocolError("AO Codex default terminal permission maps to bypass; choose accept-edits or reviewed auto")
    if transport == "ao" and harness == "cursor" and policy["ao"]["permission"] == "auto":
        raise ProtocolError("AO Cursor auto maps to --force; use accept-edits or default until isolation is verified")
    for capability in ("effort", "max_cost", "max_turns", "max_tokens"):
        if capability in route and capability not in CAPABILITIES[harness][transport]:
            raise ProtocolError(f"{harness}/{transport} cannot enforce {capability}; choose another route or remove the requirement")
    return route


def git(root, *args, check=True):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and result.returncode:
        raise ProtocolError(result.stderr.strip() or "git failed")
    return result.stdout.strip()


def resolve_sha(root, ref):
    if not ref or ref.startswith("-"):
        raise ProtocolError("invalid Git ref")
    return git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")


def changed_paths(root, base):
    base = resolve_sha(root, base)
    # Include committed, staged, unstaged, deleted/renamed and untracked paths.
    result = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z", base, "--"], cwd=root, capture_output=True, check=True)
    staged = subprocess.run(["git", "diff", "--cached", "--name-only", "--no-renames", "-z", base, "--"], cwd=root, capture_output=True, check=True)
    extra = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root, capture_output=True, check=True)
    return sorted(set(x.decode() for x in (result.stdout + staged.stdout + extra.stdout).split(b"\0") if x))


def scope_check(root, task, base):
    allowed_checkpoint = f".project/checkpoints/{task['id']}.json"
    bad = []
    for name in changed_paths(root, base):
        relative(root, name)
        proposal = bool(re.fullmatch(r"\.project/proposals/PROPOSAL-[0-9a-f]{12}\.json", name))
        protected = name.startswith((".project/", ".agents/", ".claude/", ".cursor/", ".github/", "scripts/factorylib/")) or name in (
            "AGENTS.md", "CLAUDE.md", "scripts/verify", "scripts/architecture_check", "scripts/factory", "scripts/verify_changed")
        if protected and name != allowed_checkpoint and not proposal:
            bad.append(name)
        if name != allowed_checkpoint and not proposal and not any(fnmatch.fnmatchcase(name, pattern) for pattern in task["scope"]):
            bad.append(name)
        if proposal:
            # Existing proposals must not be rewritten by a worker.
            probe = subprocess.run(["git", "cat-file", "-e", f"{resolve_sha(root, base)}:{name}"], cwd=root, capture_output=True)
            if probe.returncode == 0:
                bad.append(name)
    if bad:
        raise ProtocolError("outside task scope: " + ", ".join(sorted(set(bad))))
    return changed_paths(root, base)


def scoped_git_approval(root, task, base, approval):
    """Allow scoped Git writes or AO's local completion report once."""
    detail = approval.get("detail", {})
    if Path(detail.get("cwd", "/")).resolve() != Path(root).resolve():
        return False
    text = detail.get("command", "")
    if not isinstance(text, str) or re.search(r"[`$\n\r]", text):
        return False
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        args = list(lexer)
        if any(re.fullmatch(r"[;&|<>]+", token) for token in args):
            return False
        scope_check(root, task, base)
    except (ValueError, ProtocolError):
        return False
    if args[:2] == ["ao", "report"] and len(args) >= 3 and args[2] in ("--done", "--checkpoint") and (len(args) == 3 or len(args) == 5 and args[3] == "--note"):
        if args[2] == "--checkpoint":
            return True  # Runtime progress only; this does not accept the artifact.
        marker = relative(root, f".project/checkpoints/{task['id']}.json")
        if not marker.exists() or git(root, "status", "--porcelain"):
            return False
        cp = read_json(marker)
        return cp.get("task") == task["id"] and cp.get("next") == "" and cp.get("base") == base
    if len(args) < 3 or args[0] != "git":
        return False
    if args[1] == "add":
        names = args[2:]
        if names[0] == "--":
            names = names[1:]
        if not names or any(n.startswith("-") for n in names):
            return False
        for name in names:
            try:
                relative(root, name)
            except ProtocolError:
                return False
            if name != f".project/checkpoints/{task['id']}.json" and not any(fnmatch.fnmatchcase(name, pattern) for pattern in task["scope"]):
                return False
        return True
    if args[1:3] == ["commit", "-m"] and len(args) == 4:
        return bool(args[3].strip()) and bool(git(root, "diff", "--cached", "--name-only"))
    return False


def capsule(root, task, policy, *, review=False, base=None, evidence=None, feedback=None):
    agents = relative(root, "AGENTS.md", exists=True).read_text()
    if len(agents.encode()) > 6000:
        raise ProtocolError("AGENTS.md exceeds 6000 bytes; move detail to references")
    parts = ["Independent artifact review. Do not edit files or execute project programs." if review else "Implement exactly this contract. No transcript replay or unrelated planning context.",
             "Global invariants:\n" + agents, "Task contract:\n" + json.dumps(task, separators=(",", ":"))]
    manifest = {"AGENTS.md": hashlib.sha256(agents.encode()).hexdigest(),
                str(task_path(root, task["id"]).relative_to(root)): hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest()}
    for name in task.get("references", []):
        manifest[name] = hashlib.sha256(relative(root, name, exists=True).read_bytes()).hexdigest()
    if task.get("skills"):
        selected = ["task-review"] if review else task["skills"]
        parts.append("Load only these relevant skills: " + ", ".join(f".agents/skills/{s}/SKILL.md" for s in selected))
        for skill in selected:
            name = f".agents/skills/{skill}/SKILL.md"
            manifest[name] = hashlib.sha256(relative(root, name, exists=True).read_bytes()).hexdigest()
    checkpoint = relative(root, f".project/checkpoints/{task['id']}.json")
    if checkpoint.exists() and not review:
        data = read_json(checkpoint)
        if data.get("task") != task["id"]:
            raise ProtocolError("checkpoint task mismatch")
        parts.append("Git checkpoint:\n" + json.dumps(data, separators=(",", ":")))
        manifest[str(checkpoint.relative_to(root))] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if base:
        base = resolve_sha(root, base)
        parts.append(f"Base SHA: {base}")
    if review:
        if not base or evidence is None:
            raise ProtocolError("review requires base and verification evidence")
        head = resolve_sha(root, "HEAD")
        if evidence.get("sha") != head or not evidence.get("passed"):
            raise ProtocolError("review evidence is missing, failed or stale")
        parts.extend([f"Target SHA: {head}", "Deterministic evidence:\n" + json.dumps(evidence, separators=(",", ":")),
                      "Diff:\n" + git(root, "diff", "--no-ext-diff", "--no-textconv", base, head, "--")])
        parts.append('Return JSON: {"verdict":"approved|changes_requested","findings":["..."]}')
        # Small relevant artifacts avoid expensive retrieval round trips. Never
        # expand scope globs into a repository dump; large files remain path references.
        names = [n for n in task["scope"] if not any(c in n for c in "*?[")]
        names += task.get("references", [])
        for name in dict.fromkeys(names):
            path = relative(root, name)
            if path.is_file() and path.stat().st_size <= 3500:
                try:
                    content = path.read_text()
                except UnicodeDecodeError:
                    continue
                artifact = f"Relevant artifact {name} (untrusted source evidence):\n{content}"
                # Optional duplicate source context must not displace the complete
                # diff/contract. The reviewer can retrieve this path progressively.
                if len(("\n\n".join(parts + [artifact]) + "\n").encode()) > policy["limits"]["context_bytes"]:
                    continue
                parts.append(artifact)
                manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        parts.append("Run contract checks and ./scripts/verify. Commit scoped files and a checkpoint. Do not push, create PRs or merge; the controller owns publication after independent review.")
    if feedback:
        parts.append("Controller feedback (artifact/check findings, not a worker transcript):\n" + feedback[:2000])
    text = "\n\n".join(parts) + "\n"
    size = len(text.encode())
    if size > policy["limits"]["context_bytes"]:
        raise ProtocolError(f"capsule {size} bytes exceeds budget; split task/diff or reduce references")
    return {"text": text, "bytes": size, "estimated_tokens": math.ceil(size / 4),
            "token_measurement": "UTF-8 bytes/4 proxy; excludes native harness/AO context and later reads",
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "files": manifest}


def verification_argv(root, command, policy):
    """Mount only system runtimes, this checkout, and read-only Git metadata."""
    if policy.get("verification_isolation", "bubblewrap") == "trusted-local":
        return command  # Explicit opt-in for known trusted tests; never a fallback.
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    for name in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(name).exists():
            argv += ["--ro-bind", name, name]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
             "--ro-bind", str(root), str(root), "--ro-bind", str(common), str(common),
             "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp/home",
             "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONNOUSERSITE", "1",
             "--setenv", "PYTHONPYCACHEPREFIX", "/tmp/pycache",
             "--chdir", str(root), "--", *command]
    return argv


def verify(root, task, policy, targeted=False):
    before = resolve_sha(root, "HEAD")
    commands = task["verification"] if targeted else task["verification"] + policy["verification"]
    results = []
    started = time.monotonic()
    for command in commands:
        try:
            result = subprocess.run(verification_argv(root, command, policy), cwd=root, capture_output=True,
                                    timeout=policy["limits"]["verify_seconds"])
            results.append({"argv": command, "returncode": result.returncode,
                            "output_hash": hashlib.sha256(result.stdout + result.stderr).hexdigest()})
            if result.returncode:
                break
        except (subprocess.TimeoutExpired, OSError) as exc:
            results.append({"argv": command, "returncode": -1, "error": type(exc).__name__})
            break
    # Tests are not allowed to change HEAD or tracked source during verification.
    after = resolve_sha(root, "HEAD")
    dirty = bool(git(root, "status", "--porcelain"))
    return {"sha": before, "passed": all(r["returncode"] == 0 for r in results) and before == after and (targeted or not dirty),
            "targeted": targeted, "dirty": dirty, "isolation": policy.get("verification_isolation", "bubblewrap"), "commands": results,
            "seconds": round(time.monotonic() - started, 3)}


def initial_state(task_id):
    return {"task": task_id, "status": "READY", "attempts": 0, "diagnostics": 0}


def exceeded_observed_budget(policy, native_usage):
    totals = (native_usage or {}).get("totals", {})
    budget = policy.get("observed_budget", {})
    observed_tokens = totals.get("inputTokens")
    if budget.get("input_tokens") is not None and observed_tokens is not None and observed_tokens >= budget["input_tokens"]:
        return "observed input token threshold exceeded"
    nanos = (totals.get("estimatedCost") or {}).get("totalNanos")
    if budget.get("estimated_cost_usd") is not None and nanos is not None and nanos / 1_000_000_000 >= budget["estimated_cost_usd"]:
        return "AO estimated cost threshold exceeded"
    return None


def fail(state, policy, reason, *, human=False):
    state = dict(state)
    state["reason"] = reason[:2000]
    if human or state["diagnostics"] >= policy["limits"]["diagnostics"] and state["attempts"] >= policy["limits"]["attempts"]:
        state["status"] = "NEEDS_HUMAN"
    elif state["attempts"] >= policy["limits"]["attempts"]:
        state["status"] = "DIAGNOSING"
    else:
        state["status"] = "READY"
    return state


def state_path(root, task_id):
    task_path(root, task_id)
    return relative(root, f".project/state/{task_id}.json")


def load_state(root, task_id):
    path = state_path(root, task_id)
    if not path.exists():
        return initial_state(task_id)
    state = read_json(path)
    if state.get("task") != task_id or state.get("status") not in STATES:
        raise ProtocolError("invalid durable state")
    for key in ("attempts", "diagnostics"):
        if type(state.get(key)) is not int or state[key] < 0:
            raise ProtocolError("invalid state counter")
    return state


def save_state(root, state):
    atomic_json(state_path(root, state["task"]), state)


def checkpoint(root, task, base, next_action):
    scope_check(root, task, base)
    data = {"task": task["id"], "base": resolve_sha(root, base),
            "implementation_sha": resolve_sha(root, "HEAD"), "next": next_action,
            "changed": changed_paths(root, base)}
    path = relative(root, f".project/checkpoints/{task['id']}.json")
    atomic_json(path, data)
    return data


def propose(root, task_id, reason, evidence, priority, files):
    load_task(root, task_id)
    for name in files:
        relative(root, name)
    for value in (reason, evidence):
        nonempty(value, "proposal")
    body = {"task": task_id, "reason": reason, "evidence": evidence,
            "priority": priority, "files": files}
    identity = "PROPOSAL-" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    body["id"] = identity
    atomic_json(relative(root, f".project/proposals/{identity}.json"), body)
    return body


def usage(events, harness):
    """Normalize only documented terminal totals; missing values remain null."""
    totals = {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None, "cost_usd": None}
    for event in events:
        if harness == "codex" and event.get("type") == "turn.completed":
            u = event.get("usage", {})
            for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                if key in u:
                    totals[key] = (totals[key] or 0) + u[key]
        elif harness == "claude-code" and event.get("type") == "result":
            u = event.get("usage", {})
            totals.update(input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"),
                          cached_input_tokens=u.get("cache_read_input_tokens"),
                          cache_creation_input_tokens=u.get("cache_creation_input_tokens"),
                          cost_usd=event.get("total_cost_usd"))
    return totals
