"""Small operator CLI; AO remains the execution engine."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from .ao import AO, LazyAO
from .github import GitHub
from .harness import review_json, run as run_harness
from .protocol import (ProtocolError, atomic_json, capsule, checkpoint, exceeded_observed_budget, fail, git,
                       load_policy, load_state, load_task, propose, read_json,
                       relative, resolve_sha, route_task, save_state, scope_check,
                       scoped_git_approval, task_path, usage, verify)


def output(data):
    print(json.dumps(data, indent=2))


def root_for(value):
    return Path(value or git(Path.cwd(), "rev-parse", "--show-toplevel")).resolve()


@contextmanager
def lock(root):
    directory = root / ".factory-runtime"
    directory.mkdir(exist_ok=True)
    with (directory / "controller.lock").open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProtocolError("another controller owns this repository") from exc
        yield


def persist(root, state):
    """Commit only controller-owned state, leaving unrelated changes untouched."""
    save_state(root, state)
    name = f".project/state/{state['task']}.json"
    git(root, "add", "--", name)
    if git(root, "diff", "--cached", "--name-only", "--", name):
        git(root, "commit", "--only", "-m", f"chore: {state['task']} {state['status']}", "--", name)


def validate(root):
    load_policy(root)
    tasks = {p.stem: load_task(root, p.stem) for p in sorted((root / ".project/tasks").glob("*.json"))}
    visiting, visited = set(), set()
    def visit(task_id):
        if task_id in visiting:
            raise ProtocolError("task dependency cycle")
        if task_id not in tasks:
            raise ProtocolError(f"missing dependency {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in tasks[task_id].get("depends_on", []):
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)
    for task_id in tasks:
        visit(task_id)
        load_state(root, task_id)
    return {"valid": True, "tasks": len(tasks)}


def setup(target, source):
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    if not (target / ".git").exists():
        subprocess.run(["git", "init", "-b", "main", str(target)], check=True, capture_output=True)
    # Do not overwrite an existing project's rules, verify command or workflow.
    files = ["AGENTS.md", "CLAUDE.md", "ARCHITECTURE.md", ".gitignore", ".project/PROJECT.md",
             ".project/policy.json", "docs/protocol.md", "docs/reuse.md"]
    files += [str(p.relative_to(source)) for name in ("scripts", ".agents/skills")
              for p in (source / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    copied, existing = [], []
    for name in files:
        origin, destination = source / name, relative(target, name)
        if destination.exists() or destination.is_symlink():
            existing.append(name)
            continue
        if origin.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
            copied.append(name)
    bridge = target / ".claude/skills"
    if not bridge.exists() and not bridge.is_symlink():
        bridge.parent.mkdir(exist_ok=True)
        bridge.symlink_to("../.agents/skills", target_is_directory=True)
    ignore = target / ".gitignore"
    text = ignore.read_text() if ignore.exists() else ""
    for line in (".factory-runtime/", "__pycache__/", "*.pyc"):
        if line not in text.splitlines():
            text += "\n" + line + "\n"
    ignore.write_text(text)
    return {"copied": copied, "preserved_existing": existing,
            "next": "Adapt AGENTS/policy/verify to your repository; validate and commit before registering with AO."}


def dispatch(root, task, policy, ao, project, override=None):
    state = load_state(root, task["id"])
    if state["status"] != "READY":
        raise ProtocolError(f"cannot dispatch {state['status']}; use recover for interrupted dispatch")
    for dependency in task.get("depends_on", []):
        if load_state(root, dependency)["status"] != "MERGED":
            raise ProtocolError(f"dependency {dependency} must be merged")
    if git(root, "status", "--porcelain"):
        raise ProtocolError("commit project state before dispatch")
    route = route_task(policy, task, override)
    prior_path = root / ".factory-runtime" / f"{task['id']}.json"
    prior = read_json(prior_path) if prior_path.exists() else None
    source, retry_base, retry_head = root, None, None
    if prior and state["attempts"] and prior.get("session"):
        previous_worktree = Path(prior["worktree"]) if prior.get("worktree") else ao.workspace(prior["session"])
        if not git(previous_worktree, "status", "--porcelain"):
            try:
                scope_check(previous_worktree, task, prior["base"])
                retry_head = resolve_sha(previous_worktree, "HEAD")
                retry_base = prior["base"]
                source = previous_worktree
            except ProtocolError:
                pass  # A scope-violating checkpoint is not a reusable seed.
    # Preflight capsule before mutating state; final base includes the RUNNING commit.
    capsule(source, task, policy, base=retry_base or "HEAD", feedback=state.get("reason"))
    run_id = uuid.uuid4().hex
    state.update(status="RUNNING", attempts=state["attempts"] + 1, run_id=run_id)
    persist(root, state)
    base = retry_base or resolve_sha(root, "HEAD")
    state["base"] = base
    context = capsule(source, task, policy, base=base, feedback=state.get("reason"))
    # An explicit unique branch is also a safe recovery handle on older AO releases
    # whose clientRequestId field is ignored. AO still owns worktree creation.
    branch = f"factory/{task['id'].lower()}-{run_id[:12]}"
    git(root, "branch", branch, retry_head or base)
    request = AO.spawn_request(project, task["id"], route, context, policy, run_id, branch)
    receipt_path = root / ".factory-runtime" / f"{task['id']}.json"
    # Write request BEFORE contacting AO. Recovery uses session lookup, not replay.
    receipt = {"request": request, "route": route, "capsule": {k: v for k, v in context.items() if k != "text"},
               "started": time.time(), "base": base, "seed_sha": retry_head}
    seed_checkpoint = source / f".project/checkpoints/{task['id']}.json"
    receipt["initial_checkpoint_hash"] = hashlib.sha256(seed_checkpoint.read_bytes()).hexdigest() if seed_checkpoint.exists() else None
    if prior:
        atomic_json(root / ".factory-runtime/history" / f"{task['id']}-{state['attempts'] - 1}.json", prior)
    atomic_json(receipt_path, receipt)
    save_state(root, state)
    result = ao.request("POST", "sessions", request)
    receipt.update(response=result, session=result["session"]["id"])
    atomic_json(receipt_path, receipt)
    persist(root, state)
    return {"state": state, "session": receipt["session"], "context_bytes": context["bytes"],
            "ao_prompt_bytes": result.get("promptBytes"), "ao_system_bytes": result.get("systemPromptBytes")}


def recover(root, task, ao):
    state = load_state(root, task["id"])
    receipt_path = root / ".factory-runtime" / f"{task['id']}.json"
    if state["status"] != "RUNNING":
        raise ProtocolError("recovery applies only to a pending/running dispatch")
    if not receipt_path.exists():
        # Git contains truth, but not a safe replay handle. Do not duplicate work.
        state.update(status="NEEDS_HUMAN", reason="runtime delivery receipt lost; inspect AO before scheduling a fresh worker")
        persist(root, state)
        return state
    receipt = read_json(receipt_path)
    if receipt["request"].get("clientRequestId") != state.get("run_id"):
        raise ProtocolError("receipt generation mismatch")
    if receipt.get("session"):
        session = ao.session(receipt["session"])
        if session.get("projectId") != receipt["request"]["projectId"]:
            raise ProtocolError("recovery session/project mismatch")
    else:
        session = ao.find_session(receipt["request"]["projectId"], receipt["request"].get("branch"), receipt["request"]["displayName"])
        if session is None:
            state.update(status="NEEDS_HUMAN", reason="uncertain spawn delivery; no matching AO session found; request was not resent")
            persist(root, state)
            return state
    receipt["session"] = session["id"]
    atomic_json(receipt_path, receipt)
    state["base"] = receipt["base"]
    persist(root, state)
    return {"session": receipt["session"], "recovered_without_resending": True}


def tick(root, task, policy, ao):
    """One bounded deterministic observation. Unknown signals never imply success."""
    state = load_state(root, task["id"])
    permission_recheck = state["status"] == "NEEDS_HUMAN" and state.get("reason") == "native harness requests permission/input; inspect AO"
    if state["status"] not in ("RUNNING", "VERIFYING") and not permission_recheck:
        return state
    receipt_path = root / ".factory-runtime" / f"{task['id']}.json"
    receipt = read_json(receipt_path)
    if state["status"] == "VERIFYING" or receipt.get("worker_stopped"):
        state["status"] = "VERIFYING"
        return evaluate_artifact(root, task, policy, state, receipt, receipt_path)
    if "session" not in receipt:
        return recover(root, task, ao)
    session = ao.session(receipt["session"])
    if policy.get("observed_budget"):
        try:
            native_usage = ao.usage(receipt["session"])
        except ProtocolError:
            native_usage = None  # Unknown is not zero or an enforced hard limit.
        reason = exceeded_observed_budget(policy, native_usage)
        if reason:
            state = fail(state, policy, reason, human=True)
            receipt["ao_usage"] = native_usage
            atomic_json(receipt_path, receipt)
            persist(root, state)
            ao.exit_agent(receipt["session"])
            return state
    if time.time() - receipt["started"] > policy["limits"]["task_seconds"]:
        ao.exit_agent(receipt["session"])
        state = fail(state, policy, "task wall time budget exhausted", human=True)
        persist(root, state)
        return state
    activity = session.get("activity", {}).get("state")
    if activity in ("blocked", "waiting_input", "needs_input"):
        worktree = ao.workspace(receipt["session"])
        approvals = ao.pending_approvals(receipt["session"])
        if approvals and all(scoped_git_approval(worktree, task, state["base"], a) for a in approvals):
            for approval in approvals:
                ao.approve_once(receipt["session"], approval)
            state.update(status="RUNNING", reason="approved scoped Git/AO operation once")
            persist(root, state)
            return {**state, "approved_scoped_operation_once": len(approvals)}
        state = fail(state, policy, "native harness requests permission/input; inspect AO", human=True)
        persist(root, state)
        return state
    if activity != "idle":
        return {**state, "observation": activity or "unknown; no transition"}
    worktree = ao.workspace(receipt["session"])
    # An idle initial prompt is not completion: require a committed checkpoint.
    marker = worktree / f".project/checkpoints/{task['id']}.json"
    if not marker.exists():
        return {**state, "observation": "idle without checkpoint; wait within task deadline"}
    cp = read_json(marker)
    if receipt.get("initial_checkpoint_hash") == hashlib.sha256(marker.read_bytes()).hexdigest():
        return {**state, "observation": "inherited checkpoint unchanged; wait for this invocation"}
    committed_cp = subprocess.run(["git", "show", f"HEAD:.project/checkpoints/{task['id']}.json"], cwd=worktree, capture_output=True)
    if committed_cp.returncode or committed_cp.stdout != marker.read_bytes():
        return {**state, "observation": "checkpoint not yet committed; wait within deadline"}
    if cp.get("next"):
        ao.exit_agent(receipt["session"])
        receipt["worktree"] = str(worktree)
        atomic_json(receipt_path, receipt)
        state = fail(state, policy, "worker left incomplete Git checkpoint")
        state["checkpoint_branch"] = session.get("branch")
        persist(root, state)
        return state
    if cp.get("task") != task["id"] or cp.get("base") != state["base"]:
        raise ProtocolError("worker checkpoint does not match dispatched contract/base")
    ao.exit_agent(receipt["session"])
    try:
        receipt["ao_usage"] = ao.usage(receipt["session"])
    except ProtocolError:
        receipt["ao_usage"] = None
    state["status"] = "VERIFYING"
    state["checkpoint_branch"] = session.get("branch")
    receipt.update(worktree=str(worktree), verification_target=resolve_sha(worktree, "HEAD"), worker_stopped=True)
    atomic_json(receipt_path, receipt)
    persist(root, state)
    return evaluate_artifact(root, task, policy, state, receipt, receipt_path)


def evaluate_artifact(root, task, policy, state, receipt, receipt_path):
    """Resume verification from a stopped worker's Git artifact, without AO calls."""
    worktree = Path(receipt["worktree"])
    # Use trusted controller commands/contract, not policy edited by the worker.
    try:
        if resolve_sha(worktree, "HEAD") != receipt["verification_target"]:
            raise ProtocolError("artifact changed after worker termination")
        changed = scope_check(worktree, task, state["base"])
        evidence = verify(worktree, task, policy)
        receipt.update(worktree=str(worktree), evidence=evidence, changed=changed,
                       finished=time.time())
        atomic_json(receipt_path, receipt)
        if not evidence["passed"]:
            state = fail(state, policy, "deterministic verification failed")
        elif resolve_sha(worktree, "HEAD") == state["base"]:
            state = fail(state, policy, "no implementation commit")
        else:
            state.update(status="REVIEWING", target=evidence["sha"])
    except ProtocolError as exc:
        state = fail(state, policy, str(exc))
    persist(root, state)
    return state


def record_review(root, task, policy, review_path):
    state = load_state(root, task["id"])
    if state["status"] != "REVIEWING":
        raise ProtocolError("task is not awaiting review")
    review = read_json(review_path)
    receipt = read_json(root / ".factory-runtime" / f"{task['id']}.json")
    worktree = Path(receipt["worktree"])
    target = resolve_sha(worktree, "HEAD")
    if target != state["target"] or review.get("sha") != target:
        raise ProtocolError("review target is stale")
    if review.get("role") != "independent-reviewer" or not review.get("run_id") or review["run_id"] == state["run_id"]:
        raise ProtocolError("review must come from a fresh independent invocation")
    if review.get("verdict") not in ("approved", "changes_requested") or not isinstance(review.get("findings"), list) or any(not isinstance(item, str) for item in review["findings"]):
        raise ProtocolError("invalid review verdict/findings")
    if review["verdict"] == "approved":
        state.update(status="ACCEPTED", review=review)
    else:
        state = fail(state, policy, "review requested changes: " + "; ".join(review["findings"]))
    persist(root, state)
    return state


def independent_review(root, task, policy):
    state = load_state(root, task["id"])
    if state["status"] != "REVIEWING":
        raise ProtocolError("task is not awaiting independent review")
    receipt = read_json(root / ".factory-runtime" / f"{task['id']}.json")
    worktree = Path(receipt["worktree"])
    route = route_task(policy, task, transport="cli", review=True)
    scope_check(worktree, task, state["base"])
    context = capsule(worktree, task, policy, review=True, base=state["base"], evidence=receipt["evidence"])
    result = run_harness(worktree, route, context["text"], "review", policy["limits"]["task_seconds"])
    if not result["passed"]:
        state = fail(state, policy, "independent reviewer failed/deadline; inspect compact runtime report")
        persist(root, state)
        return state
    try:
        verdict = review_json(result["final"])
    except ProtocolError as exc:
        state = fail(state, policy, str(exc))
        persist(root, state)
        return state
    verdict.update(role="independent-reviewer", sha=state["target"], run_id=result["run_id"],
                   usage=result["usage"], context_bytes=result["context_bytes"])
    path = root / ".factory-runtime" / f"{task['id']}-review.json"
    atomic_json(path, verdict)
    return record_review(root, task, policy, path)


def diagnose(root, task, policy, ao):
    state = load_state(root, task["id"])
    if state["status"] != "DIAGNOSING":
        raise ProtocolError("task is not awaiting diagnosis")
    receipt = read_json(root / ".factory-runtime" / f"{task['id']}.json")
    worktree = ao.workspace(receipt["session"])
    route = route_task(policy, {**task, "risk": "strong"}, transport="cli")
    text = "Independent read-only diagnosis. Do not edit or execute project programs.\n"
    text += json.dumps({"task": task, "failure": state.get("reason"), "base": state["base"],
                        "verification": receipt.get("evidence")}, separators=(",", ":"))
    text += "\nInspect only relevant code/diff. Return JSON {\"cause\":\"...\",\"next_action\":\"...\",\"needs_human\":false}."
    if len(text.encode()) > policy["limits"]["context_bytes"]:
        state = fail(state, policy, "diagnostic capsule exceeds context budget", human=True)
        persist(root, state)
        return state
    result = run_harness(worktree, route, text, "review", min(180, policy["limits"]["task_seconds"]))
    state["diagnostics"] += 1
    try:
        diagnostic = json.loads(result["final"])
        if not result["passed"] or not isinstance(diagnostic, dict) or type(diagnostic.get("needs_human")) is not bool or not isinstance(diagnostic.get("next_action"), str) or not diagnostic["next_action"].strip():
            raise ProtocolError("diagnostic unavailable")
        state.update(status="NEEDS_HUMAN" if diagnostic["needs_human"] else "READY",
                     reason=f"Diagnosis: {diagnostic.get('cause', '')}. Next: {diagnostic['next_action']}"[:2000])
    except (ValueError, ProtocolError):
        state = fail(state, policy, "independent diagnosis failed", human=True)
    state["diagnostic_usage"] = result["usage"]
    persist(root, state)
    return state


def merge_local(root, task, policy):
    state = load_state(root, task["id"])
    if state["status"] != "ACCEPTED" or git(root, "status", "--porcelain"):
        raise ProtocolError("local merge requires accepted task and clean control checkout")
    receipt = read_json(root / ".factory-runtime" / f"{task['id']}.json")
    if resolve_sha(root, state["checkpoint_branch"]) != state["target"]:
        raise ProtocolError("branch changed after review")
    if git(root, "remote"):
        raise ProtocolError("use protected PR workflow for repositories with a remote")
    scope_check(Path(receipt["worktree"]), task, state["base"])
    git(root, "merge", "--no-ff", "--no-edit", state["target"])
    evidence = verify(root, task, policy)
    if not evidence["passed"]:
        state = fail(state, policy, "post-merge verification failed", human=True)
    else:
        state["status"] = "MERGED"
    persist(root, state)
    return state


def remote_step(root, task, policy):
    """One publish/CI/merge observation; no LLM or alternative SCM state store."""
    state = load_state(root, task["id"])
    if state["status"] not in ("ACCEPTED", "PR_OPEN"):
        raise ProtocolError("remote lifecycle requires accepted work")
    if not policy["publish"]:
        return {**state, "action": "publishing disabled by policy"}
    try:
        if git(root, "status", "--porcelain"):
            raise ProtocolError("remote operation requires clean controller checkout")
        receipt_path = root / ".factory-runtime" / f"{task['id']}.json"
        receipt = read_json(receipt_path)
        worktree = Path(receipt["worktree"])
        if resolve_sha(worktree, "HEAD") != state["target"] or state["review"]["sha"] != state["target"] or state["review"]["verdict"] != "approved":
            raise ProtocolError("artifact changed after independent review")
        scope_check(worktree, task, state["base"])
        if git(worktree, "status", "--porcelain"):
            raise ProtocolError("reviewed worker checkout is dirty")
        host = GitHub(root, policy.get("github", {}))
        if state["status"] == "ACCEPTED":
            body = f"{task['goal']}\n\nValidation: contract checks and ./scripts/verify passed at `{state['target']}`.\nIndependent artifact review: approved (`{state['review']['run_id']}`).\n\nWorker scope: " + ", ".join(task["scope"])
            pr = host.publish(state["checkpoint_branch"], state["target"], f"{task['id']}: {task['title']}", body)
            state.update(status="PR_OPEN", pr={"number": pr["number"], "url": pr["html_url"]})
            persist(root, state)
            return state
        number = state["pr"]["number"]
        result = host.merge(number, state["target"], state["checkpoint_branch"]) if policy["auto_merge"] else host.gate(number, state["target"], state["checkpoint_branch"])
        if not result.get("merged"):
            return {**state, "remote_observation": result}
        if state.get("remote_merge_sha") != result["merge_sha"]:
            git(root, "fetch", "origin", host.base)
            merge_sha = resolve_sha(root, result["merge_sha"])
            if subprocess.run(["git", "merge-base", "--is-ancestor", merge_sha, "FETCH_HEAD"], cwd=root).returncode:
                raise ProtocolError("merged PR is absent from fetched base")
            git(root, "merge", "--no-edit", "FETCH_HEAD")
            evidence = verify(root, task, policy)
            if not evidence["passed"]:
                raise ProtocolError("post-merge verification failed")
            state["remote_merge_sha"] = merge_sha
        main_checks = host.checks(state["remote_merge_sha"])
        receipt["remote_merge"] = result
        atomic_json(receipt_path, receipt)
        if not main_checks["ready"]:
            persist(root, state)
            return {**state, "remote_observation": main_checks}
        state["status"] = "MERGED"
    except (ProtocolError, subprocess.TimeoutExpired) as exc:
        state["blocked_status"] = state["status"]
        state = fail(state, policy, f"remote lifecycle: {exc}", human=True)
    persist(root, state)
    return state


def queue_step(root, policy, ao, project, local_merge=False, pause_after=None):
    tasks = [load_task(root, p.stem) for p in sorted((root / ".project/tasks").glob("*.json"))]
    for task in tasks:
        state = load_state(root, task["id"])
        if pause_after and task.get("milestone") == pause_after and state["status"] == "MERGED":
            members = [t for t in tasks if t.get("milestone") == pause_after]
            if all(load_state(root, t["id"])["status"] == "MERGED" for t in members):
                return {"paused_after_milestone": pause_after}
        if state["status"] in ("RUNNING", "VERIFYING"):
            return tick(root, task, policy, ao)
        if state["status"] == "REVIEWING":
            return independent_review(root, task, policy)
        if state["status"] == "DIAGNOSING":
            return diagnose(root, task, policy, ao)
        if state["status"] in ("ACCEPTED", "PR_OPEN"):
            if policy["publish"]:
                return remote_step(root, task, policy)
            return merge_local(root, task, policy) if local_merge and state["status"] == "ACCEPTED" else {"task": task["id"], "status": state["status"], "action": "await protected PR merge"}
    # Serial default: no extra agents; independent tasks can continue past a human block.
    for task in tasks:
        if load_state(root, task["id"])["status"] == "READY" and all(load_state(root, d)["status"] == "MERGED" for d in task.get("depends_on", [])):
            return dispatch(root, task, policy, ao, project)
    return {"idle": True, "needs_human": [load_state(root, t["id"]) for t in tasks
                                          if load_state(root, t["id"])["status"] in ("NEEDS_HUMAN", "DIAGNOSING")]}


def parser():
    p = argparse.ArgumentParser(description="Portable Git protocol and policy over upstream AO")
    p.add_argument("--root")
    p.add_argument("--run-file")
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("path")
    commands.add_parser("validate")
    status = commands.add_parser("status")
    status.add_argument("--needs-human", action="store_true")
    for name in ("capsule", "scope", "verify", "checkpoint", "route", "dispatch", "recover", "tick", "review-record", "review-run", "diagnostic-complete", "resume", "merge-local", "remote-step"):
        sub = commands.add_parser(name)
        sub.add_argument("task")
        if name in ("capsule", "scope", "checkpoint"):
            sub.add_argument("--base", required=name != "capsule")
        if name in ("route", "dispatch"):
            sub.add_argument("--provider", choices=("codex", "claude-code", "cursor", "grok"))
        if name == "route":
            sub.add_argument("--transport", choices=("ao", "cli"), default="ao")
        if name == "dispatch":
            sub.add_argument("--project", required=True)
        if name == "capsule":
            sub.add_argument("--review", action="store_true")
            sub.add_argument("--evidence")
        if name == "verify":
            sub.add_argument("--targeted", action="store_true")
        if name == "checkpoint":
            sub.add_argument("--next", required=True)
        if name == "review-record":
            sub.add_argument("file")
        if name in ("diagnostic-complete", "resume"):
            sub.add_argument("--reason", required=True)
    proposal = commands.add_parser("propose")
    proposal.add_argument("--task", required=True)
    proposal.add_argument("--reason", required=True)
    proposal.add_argument("--evidence", required=True)
    proposal.add_argument("--priority", choices=("low", "normal", "high"), default="normal")
    proposal.add_argument("--files", nargs="+", required=True)
    report = commands.add_parser("report")
    report.add_argument("task")
    report.add_argument("--events")
    report.add_argument("--harness", choices=("codex", "claude-code"))
    register = commands.add_parser("register")
    register.add_argument("--project", required=True)
    queue = commands.add_parser("queue")
    queue.add_argument("--project", required=True)
    queue.add_argument("--steps", type=int, default=20)
    queue.add_argument("--interval", type=int, default=5)
    queue.add_argument("--local-merge", action="store_true")
    queue.add_argument("--pause-after-milestone")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            output(setup(args.path, Path(__file__).resolve().parents[2]))
            return
        root = root_for(args.root)
        policy = load_policy(root)
        if args.command == "validate":
            output(validate(root))
            return
        if args.command == "status":
            states = [load_state(root, p.stem) for p in sorted((root / ".project/tasks").glob("*.json"))]
            output([s for s in states if not args.needs_human or s["status"] == "NEEDS_HUMAN"])
            return
        if args.command == "register":
            ao = AO(args.run_file)
            config = {"defaultBranch": git(root, "branch", "--show-current"),
                      "agentConfig": {"permissions": policy["ao"]["permission"]},
                      "worker": {"agent": policy["routing"]["standard"]["harness"]},
                      "orchestrator": {"agent": policy["routing"]["strong"]["harness"]},
                      "reviewers": [{"harness": policy["routing"]["review"]["harness"]}],
                      "agentRules": "Workers implement, verify and commit a task checkpoint. Do not push branches, create PRs or merge: factory controller owns publication after independent review.",
                      "orchestratorRules": "Persist product/task decisions in Git. Load task-planning only for planning. Dispatch bounded contracts through scripts/factory. No transcript transfer."}
            result = ao.request("POST", "projects", {"projectId": args.project, "path": str(root),
                                                     "name": args.project, "config": config})
            output(result)
            return
        if args.command == "queue":
            if not 1 <= args.steps <= 10000 or not 1 <= args.interval <= 60:
                raise ProtocolError("queue requires 1..10000 steps and 1..60 second interval")
            validate(root)
            ao = LazyAO(args.run_file)
            previous = None
            for step in range(args.steps):
                policy = load_policy(root)
                with lock(root):
                    result = queue_step(root, policy, ao, args.project, args.local_merge, args.pause_after_milestone)
                if result != previous:
                    output(result)
                    sys.stdout.flush()
                    previous = result
                if result.get("idle") or result.get("paused_after_milestone"):
                    break
                if step + 1 < args.steps:
                    time.sleep(args.interval)
            return
        task = load_task(root, args.task)
        if args.command == "route":
            output(route_task(policy, task, args.provider, args.transport))
        elif args.command == "capsule":
            result = capsule(root, task, policy, review=args.review, base=args.base,
                             evidence=read_json(args.evidence) if args.evidence else None)
            output(result)
        elif args.command == "scope":
            output({"changed": scope_check(root, task, args.base), "passed": True})
        elif args.command == "verify":
            result = verify(root, task, policy, args.targeted)
            output(result)
            if not result["passed"]:
                sys.exit(1)
        elif args.command == "checkpoint":
            output(checkpoint(root, task, args.base, args.next))
        elif args.command == "propose":
            output(propose(root, args.task, args.reason, args.evidence, args.priority, args.files))
        elif args.command == "report":
            state = load_state(root, task["id"])
            receipt_path = root / ".factory-runtime" / f"{task['id']}.json"
            receipt = read_json(receipt_path) if receipt_path.exists() else {}
            events = [json.loads(line) for line in Path(args.events).read_text().splitlines() if line.strip()] if args.events else []
            result = {"task": task["id"], "result": state["status"], "attempts": state["attempts"],
                      "context": receipt.get("capsule"), "ao_prompt_bytes": receipt.get("response", {}).get("promptBytes"),
                      "ao_system_bytes": receipt.get("response", {}).get("systemPromptBytes"),
                      "usage": usage(events, args.harness), "review_usage": state.get("review", {}).get("usage"),
                      "ao_usage": {k: receipt.get("ao_usage", {}).get(k) for k in ("incomplete", "totals")} if receipt.get("ao_usage") else None,
                      "duration_seconds": round(receipt.get("finished", time.time()) - receipt["started"], 3) if receipt.get("started") else None}
            output(result)
        else:
            with lock(root):
                if args.command == "dispatch":
                    output(dispatch(root, task, policy, AO(args.run_file), args.project, args.provider))
                elif args.command == "recover":
                    output(recover(root, task, AO(args.run_file)))
                elif args.command == "tick":
                    output(tick(root, task, policy, LazyAO(args.run_file)))
                elif args.command == "review-record":
                    output(record_review(root, task, policy, args.file))
                elif args.command == "review-run":
                    output(independent_review(root, task, policy))
                elif args.command in ("diagnostic-complete", "resume"):
                    state = load_state(root, task["id"])
                    expected = "DIAGNOSING" if args.command == "diagnostic-complete" else "NEEDS_HUMAN"
                    if state["status"] != expected:
                        raise ProtocolError(f"expected {expected}")
                    if args.command == "diagnostic-complete":
                        state["diagnostics"] += 1
                    else:
                        if state.get("blocked_status") not in ("ACCEPTED", "PR_OPEN"):
                            state["attempts"], state["diagnostics"] = 0, 0
                    state.update(status=state.pop("blocked_status", "READY"), reason=args.reason)
                    persist(root, state)
                    output(state)
                elif args.command == "merge-local":
                    output(merge_local(root, task, policy))
                elif args.command == "remote-step":
                    output(remote_step(root, task, policy))
    except (ProtocolError, KeyError, TypeError, ValueError) as exc:
        print(f"factory: {exc}", file=sys.stderr)
        sys.exit(2)
