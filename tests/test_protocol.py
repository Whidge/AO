import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factorylib.protocol import (ProtocolError, atomic_json, capsule, checkpoint, fail,
    git, initial_state, load_policy, load_state, load_task, propose, relative, scoped_git_approval, exceeded_observed_budget,
    route_task, save_state, scope_check, usage, verify)
from factorylib.cli import dispatch, recover, record_review, tick, validate, queue_step

SOURCE = Path(__file__).resolve().parents[1]


class RepoFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Protocol Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        # Fixture directories are deleted immediately; no detached Git maintenance
        # may race their teardown on runners with different global Git defaults.
        git(self.root, "config", "gc.auto", "0")
        git(self.root, "config", "maintenance.auto", "false")
        self.write(".gitignore", ".factory-runtime/\n__pycache__/\n")
        self.write("AGENTS.md", "Keep domain independent. Read only task references.\n")
        self.write("docs/relevant.md", "Relevant architecture\n")
        self.write(".project/ROADMAP.md", "UNRELATED_ROADMAP_SECRET\n" * 1000)
        self.write(".project/decisions/old.md", "UNRELATED_ADR\n")
        for skill in ("task-implementation", "task-review"):
            self.write(f".agents/skills/{skill}/SKILL.md", f"---\nname: {skill}\ndescription: test\n---\nLoad on demand\n")
        self.write(".agents/skills/unrelated/SKILL.md", "UNRELATED_SKILL")
        self.write("src/module.py", "value = 1\n")
        self.policy = json.loads((SOURCE / ".project/policy.json").read_text())
        self.policy["verification_isolation"] = "trusted-local"
        self.policy["verification"] = [[sys.executable, "-c", "assert 2+2 == 4"]]
        atomic_json(self.root / ".project/policy.json", self.policy)
        self.task = {"id": "TASK-001", "title": "Change value", "goal": "Set value to two",
                     "acceptance": ["Value equals two"], "scope": ["src/module.py"],
                     "skills": ["task-implementation"], "references": ["docs/relevant.md"],
                     "verification": [[sys.executable, "-c", "assert 1+1 == 2"]]}
        self.save_task(self.task)
        self.commit("initial")
        self.base = git(self.root, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def save_task(self, task):
        atomic_json(self.root / f".project/tasks/{task['id']}.json", task)

    def commit(self, title):
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", title)

class RepoTest(RepoFixture, unittest.TestCase):
    def test_contract_roundtrip(self):
        self.assertEqual(load_task(self.root, "TASK-001"), self.task)

    def test_duplicate_key(self):
        self.write(".project/tasks/TASK-001.json", '{"id":"TASK-001","id":"TASK-001"}')
        with self.assertRaisesRegex(ProtocolError, "duplicate"):
            load_task(self.root, "TASK-001")

    def test_unknown_field(self):
        self.task["provider"] = "codex"
        self.save_task(self.task)
        with self.assertRaisesRegex(ProtocolError, "unknown"):
            load_task(self.root, "TASK-001")

    def test_invalid_commands(self):
        self.task["verification"] = ["echo success; rm -rf unrelated"]
        self.save_task(self.task)
        with self.assertRaisesRegex(ProtocolError, "argv"):
            load_task(self.root, "TASK-001")

    def test_empty_acceptance(self):
        self.task["acceptance"] = []
        self.save_task(self.task)
        with self.assertRaises(ProtocolError):
            load_task(self.root, "TASK-001")

    def test_missing_reference(self):
        self.task["references"] = ["docs/missing.md"]
        self.save_task(self.task)
        with self.assertRaises(ProtocolError):
            load_task(self.root, "TASK-001")

    def test_traversal_and_symlink(self):
        with self.assertRaises(ProtocolError):
            relative(self.root, "../secret")
        (self.root / "escape").symlink_to("/etc")
        with self.assertRaises(ProtocolError):
            relative(self.root, "escape/passwd", exists=True)

    def test_routing_provider_override(self):
        self.policy["routing"]["standard"].update(model="provider-model", effort="high")
        self.assertEqual(route_task(self.policy, self.task, "claude-code"), {"harness": "claude-code"})

    def test_capability_differences(self):
        self.policy["routing"]["standard"] = {"harness": "claude-code", "max_cost": 1, "max_turns": 3}
        self.assertEqual(route_task(self.policy, self.task, transport="cli")["max_cost"], 1)
        with self.assertRaisesRegex(ProtocolError, "cannot enforce"):
            route_task(self.policy, self.task, transport="ao")
        with self.assertRaisesRegex(ProtocolError, "cannot enforce"):
            route_task(self.policy, self.task, "codex", transport="cli")

    def test_unknown_provider(self):
        with self.assertRaises(ProtocolError):
            route_task(self.policy, self.task, "unknown")

    def test_no_unrelated_context(self):
        result = capsule(self.root, self.task, self.policy)
        for text in ("UNRELATED_ROADMAP", "UNRELATED_ADR", "UNRELATED_SKILL", "Relevant architecture"):
            self.assertNotIn(text, result["text"])
        self.assertIn("docs/relevant.md", result["files"])
        self.assertNotIn(".project/ROADMAP.md", result["files"])
        self.assertEqual(result["bytes"], len(result["text"].encode()))

    def test_bounded_context(self):
        self.policy["limits"]["context_bytes"] = 5
        with self.assertRaisesRegex(ProtocolError, "budget"):
            capsule(self.root, self.task, self.policy)

    def test_agents_must_remain_small(self):
        self.write("AGENTS.md", "x" * 6001)
        with self.assertRaisesRegex(ProtocolError, "AGENTS"):
            capsule(self.root, self.task, self.policy)

    def test_scope_committed_and_untracked(self):
        self.write("src/module.py", "value = 2\n")
        self.commit("change")
        self.assertEqual(scope_check(self.root, self.task, self.base), ["src/module.py"])
        self.write("src/unrelated.py", "bad\n")
        with self.assertRaisesRegex(ProtocolError, "unrelated"):
            scope_check(self.root, self.task, self.base)

    def test_scope_rename_checks_both_paths(self):
        git(self.root, "mv", "src/module.py", "src/other.py")
        with self.assertRaisesRegex(ProtocolError, "other"):
            scope_check(self.root, self.task, self.base)

    def test_staged_protected_change_reversed_in_worktree_is_rejected(self):
        original = (self.root / "AGENTS.md").read_text()
        self.write("AGENTS.md", "weakened instructions\n")
        git(self.root, "add", "AGENTS.md")
        self.write("AGENTS.md", original)
        with self.assertRaisesRegex(ProtocolError, "AGENTS"):
            scope_check(self.root, self.task, self.base)
        approval = {"detail": {"cwd": str(self.root), "command": "git commit -m change"}}
        self.assertFalse(scoped_git_approval(self.root, self.task, self.base, approval))

    def test_protected_files_despite_wildcard(self):
        self.task["scope"] = ["*"]
        self.write("AGENTS.md", "weaken invariants\n")
        with self.assertRaisesRegex(ProtocolError, "AGENTS"):
            scope_check(self.root, self.task, self.base)

    def test_proposal_dedup_and_scope(self):
        a = propose(self.root, "TASK-001", "other bug", "repro", "normal", ["src/module.py"])
        b = propose(self.root, "TASK-001", "other bug", "repro", "normal", ["src/module.py"])
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(list((self.root / ".project/proposals").glob("*.json"))), 1)
        scope_check(self.root, self.task, self.base)

    def test_retry_and_diagnostic_limits(self):
        state = initial_state("TASK-001")
        state["attempts"] = 1
        self.assertEqual(fail(state, self.policy, "lint")["status"], "READY")
        state["attempts"] = 3
        self.assertEqual(fail(state, self.policy, "lint")["status"], "DIAGNOSING")
        state["diagnostics"] = 1
        self.assertEqual(fail(state, self.policy, "still broken")["status"], "NEEDS_HUMAN")
        self.policy["limits"]["diagnostics"] = 0
        state["diagnostics"] = 0
        self.assertEqual(fail(state, self.policy, "still broken")["status"], "NEEDS_HUMAN")

    def test_permission_or_budget_escalation(self):
        self.assertEqual(fail(initial_state("TASK-001"), self.policy, "credential", human=True)["status"], "NEEDS_HUMAN")

    def test_state_reloaded_without_conversation(self):
        state = initial_state("TASK-001")
        state.update(status="NEEDS_HUMAN", reason="credential missing")
        save_state(self.root, state)
        self.commit("state")
        self.assertEqual(load_state(self.root, "TASK-001"), state)
        self.assertIn("NEEDS_HUMAN", git(self.root, "show", "HEAD:.project/state/TASK-001.json"))

    def test_checkpoint_git_only_handoff(self):
        self.write("src/module.py", "value = 2\n")
        cp = checkpoint(self.root, self.task, self.base, "add edge case tests")
        self.commit("checkpoint")
        fresh = capsule(self.root, load_task(self.root, "TASK-001"), self.policy, base=self.base)
        self.assertIn("add edge case tests", fresh["text"])
        self.assertNotIn("conversation", json.dumps(cp))

    def test_verification_real_subprocess_pass_and_failure(self):
        self.assertTrue(verify(self.root, self.task, self.policy)["passed"])
        self.task["verification"] = [[sys.executable, "-c", "raise SystemExit(7)"]]
        self.assertFalse(verify(self.root, self.task, self.policy)["passed"])

    def test_verification_timeout(self):
        self.policy["limits"]["verify_seconds"] = 1
        self.task["verification"] = [[sys.executable, "-c", "import time; time.sleep(2)"]]
        self.assertFalse(verify(self.root, self.task, self.policy)["passed"])

    def test_verification_rejects_dirty_artifact(self):
        self.write("src/module.py", "value = 2\n")
        self.assertFalse(verify(self.root, self.task, self.policy)["passed"])
        self.assertTrue(verify(self.root, self.task, self.policy, targeted=True)["passed"])

    def test_review_excludes_checkpoint_and_transcript(self):
        checkpoint(self.root, self.task, self.base, "private worker continuation")
        self.commit("checkpoint")
        evidence = verify(self.root, self.task, self.policy)
        result = capsule(self.root, self.task, self.policy, review=True, base=self.base, evidence=evidence)
        self.assertNotIn("Git checkpoint:", result["text"])
        self.assertIn("Target SHA:", result["text"])
        # Checkpoint appears only as part of the artifact diff, never a replay.
        self.assertIn(".agents/skills/task-review/SKILL.md", result["files"])

    def test_stale_evidence_rejected(self):
        evidence = verify(self.root, self.task, self.policy)
        self.write("src/module.py", "value = 2\n")
        self.commit("new head")
        with self.assertRaisesRegex(ProtocolError, "stale"):
            capsule(self.root, self.task, self.policy, review=True, base=self.base, evidence=evidence)

    def test_dependencies_cycle(self):
        two = {**self.task, "id": "TASK-002", "depends_on": ["TASK-001"]}
        self.task["depends_on"] = ["TASK-002"]
        self.save_task(two)
        self.save_task(self.task)
        with self.assertRaisesRegex(ProtocolError, "cycle"):
            validate(self.root)

    def test_usage_missing_not_zero(self):
        self.assertIsNone(usage([], "codex")["input_tokens"])

    def test_usage_cumulative_terminal_events(self):
        events = [{"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10}}] * 2
        self.assertEqual(usage(events, "codex")["input_tokens"], 200)
        self.assertEqual(usage(events, "codex")["cached_input_tokens"], 40)
        self.assertEqual(usage([{"type": "result", "usage": {"input_tokens": 50}, "total_cost_usd": .1}], "claude-code")["cost_usd"], .1)

    def test_permission_bypass_rejected(self):
        self.policy["ao"]["permission"] = "bypass-permissions"
        atomic_json(self.root / ".project/policy.json", self.policy)
        with self.assertRaisesRegex(ProtocolError, "permission"):
            load_policy(self.root)

    def test_ao_codex_default_is_not_safe(self):
        self.policy["ao"]["permission"] = "default"
        with self.assertRaisesRegex(ProtocolError, "bypass"):
            route_task(self.policy, self.task)

    def test_cursor_force_mode_rejected(self):
        self.policy["ao"]["permission"] = "auto"
        with self.assertRaisesRegex(ProtocolError, "force"):
            route_task(self.policy, self.task, "cursor")

    def test_approve_only_scoped_git(self):
        self.write("src/module.py", "value = 2\n")
        detail = {"cwd": str(self.root), "command": "git add src/module.py"}
        self.assertTrue(scoped_git_approval(self.root, self.task, self.base, {"detail": detail}))
        for command in ("git add .", "git add --all", "git add src/module.py; curl evil", "git push", "git commit --amend", "git add AGENTS.md", "git add ../outside"):
            detail["command"] = command
            self.assertFalse(scoped_git_approval(self.root, self.task, self.base, {"detail": detail}), command)
        git(self.root, "add", "src/module.py")
        detail["command"] = "git commit -m 'implement task'"
        self.assertTrue(scoped_git_approval(self.root, self.task, self.base, {"detail": detail}))
        detail["cwd"] = "/tmp"
        self.assertFalse(scoped_git_approval(self.root, self.task, self.base, {"detail": detail}))

    def test_budget_measurements_do_not_assume_missing_zero(self):
        self.policy["observed_budget"] = {"input_tokens": 100, "estimated_cost_usd": 1}
        self.assertIsNone(exceeded_observed_budget(self.policy, None))
        self.assertIn("token", exceeded_observed_budget(self.policy, {"totals": {"inputTokens": 100}}))
        self.assertIn("cost", exceeded_observed_budget(self.policy, {"totals": {"estimatedCost": {"totalNanos": 1000000000}}}))


class CrashAO:
    """Only fake transport fault; repository/worktrees/commands are real."""
    def __init__(self):
        self.calls = []
        self.crash = True
    def request(self, method, path, request):
        self.calls.append(request)
        if self.crash:
            self.crash = False
            raise ProtocolError("lost response after accepted spawn")
        return {"session": {"id": "ao-1"}, "promptBytes": len(request["prompt"].encode()), "systemPromptBytes": 4000}
    def find_session(self, project, branch, display_name):
        return {"id": "ao-1", "projectId": project, "branch": branch, "displayName": display_name}


class RecoveryTest(RepoFixture, unittest.TestCase):
    def test_uncertain_delivery_without_match_never_resends(self):
        ao = CrashAO()
        with self.assertRaises(ProtocolError):
            dispatch(self.root, self.task, self.policy, ao, "test")
        ao.find_session = lambda *args: None
        self.assertEqual(recover(self.root, self.task, ao)["status"], "NEEDS_HUMAN")
        self.assertEqual(len(ao.calls), 1)
    def test_dispatch_restart_finds_session_without_resend(self):
        ao = CrashAO()
        with self.assertRaisesRegex(ProtocolError, "lost response"):
            dispatch(self.root, self.task, self.policy, ao, "test")
        self.assertEqual(load_state(self.root, "TASK-001")["attempts"], 1)
        result = recover(self.root, self.task, ao)
        self.assertEqual(result["session"], "ao-1")
        self.assertEqual(len(ao.calls), 1)
        self.assertEqual(load_state(self.root, "TASK-001")["attempts"], 1)

    def test_lost_receipt_escalates_without_new_spawn(self):
        ao = CrashAO()
        with self.assertRaises(ProtocolError):
            dispatch(self.root, self.task, self.policy, ao, "test")
        (self.root / ".factory-runtime/TASK-001.json").unlink()
        state = recover(self.root, self.task, ao)
        self.assertEqual(state["status"], "NEEDS_HUMAN")
        self.assertEqual(len(ao.calls), 1)

    def test_dispatch_does_not_resolve_unmerged_dependency(self):
        self.task["depends_on"] = ["TASK-002"]
        with self.assertRaisesRegex(ProtocolError, "merged"):
            dispatch(self.root, self.task, self.policy, CrashAO(), "test")


class ArtifactAO:
    def __init__(self, root, path):
        self.root, self.path = root, path
        self.calls = []
        self.activity = "active"
    def request(self, method, route, request):
        self.calls.append((method, route, request))
        if route == "sessions":
            self.branch = request["branch"]
            git(self.root, "worktree", "add", str(self.path), self.branch)
            return {"session": {"id": "ao-test"}, "promptBytes": len(request["prompt"].encode()), "systemPromptBytes": 1000}
        raise AssertionError(route)
    def session(self, _):
        return {"activity": {"state": self.activity}, "branch": self.branch}
    def workspace(self, _):
        return self.path
    def exit_agent(self, _):
        self.calls.append(("exit",))
    def usage(self, _):
        return {"incomplete": True, "totals": {}}


class LifecycleTest(RepoFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.worktree_tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self.worktree_tmp.name) / "worker"
        self.ao = ArtifactAO(self.root, self.worktree)
    def tearDown(self):
        self.worktree_tmp.cleanup()
        super().tearDown()
    def complete_worker(self):
        dispatch(self.root, self.task, self.policy, self.ao, "project")
        state = load_state(self.root, "TASK-001")
        (self.worktree / "src/module.py").write_text("value = 2\n")
        checkpoint(self.worktree, self.task, state["base"], "")
        git(self.worktree, "add", "src/module.py", ".project/checkpoints/TASK-001.json")
        git(self.worktree, "commit", "-m", "worker result")
        self.ao.activity = "idle"
        return tick(self.root, self.task, self.policy, self.ao)
    def test_real_git_lifecycle_and_independent_review(self):
        state = self.complete_worker()
        self.assertEqual(state["status"], "REVIEWING")
        path = self.root / ".factory-runtime/review.json"
        atomic_json(path, {"role": "independent-reviewer", "run_id": "fresh-review",
                           "sha": state["target"], "verdict": "approved", "findings": []})
        self.assertEqual(record_review(self.root, self.task, self.policy, path)["status"], "ACCEPTED")
    def test_verification_recovery_does_not_need_worker_or_live_ao(self):
        state = self.complete_worker()
        state["status"] = "VERIFYING"
        save_state(self.root, state)
        self.commit("simulate restart during verification")
        class UnavailableAO:
            def __getattr__(self, name):
                raise AssertionError("verification recovery must not call AO: " + name)
        result = tick(self.root, self.task, self.policy, UnavailableAO())
        self.assertEqual(result["status"], "REVIEWING")
        self.assertEqual(result["target"], state["target"])
    def test_public_cli_verification_recovery_without_daemon_run_file(self):
        state = self.complete_worker()
        for command in (["tick", "TASK-001"], ["queue", "--project", "test", "--steps", "1"]):
            state["status"] = "VERIFYING"
            save_state(self.root, state)
            self.commit("simulate daemon gone at public CLI")
            result = subprocess.run([sys.executable, str(SOURCE / "scripts/factory"), "--root", str(self.root),
                                     "--run-file", str(self.root / "missing-run-file"), *command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "REVIEWING")
    def test_inherited_incomplete_checkpoint_does_not_end_new_worker(self):
        dispatch(self.root, self.task, self.policy, self.ao, "project")
        state = load_state(self.root, "TASK-001")
        checkpoint(self.worktree, self.task, state["base"], "add edge cases")
        git(self.worktree, "add", ".project/checkpoints/TASK-001.json")
        git(self.worktree, "commit", "-m", "incomplete checkpoint")
        receipt_path = self.root / ".factory-runtime/TASK-001.json"
        from factorylib.protocol import read_json
        import hashlib
        receipt = read_json(receipt_path)
        receipt["initial_checkpoint_hash"] = hashlib.sha256((self.worktree / ".project/checkpoints/TASK-001.json").read_bytes()).hexdigest()
        atomic_json(receipt_path, receipt)
        self.ao.activity = "idle"
        result = tick(self.root, self.task, self.policy, self.ao)
        self.assertEqual(result["status"], "RUNNING")
        self.assertIn("inherited", result["observation"])
        self.assertFalse(any(call == ("exit",) for call in self.ao.calls))
    def test_stale_review_cannot_accept(self):
        state = self.complete_worker()
        (self.worktree / "src/module.py").write_text("value = 3\n")
        git(self.worktree, "add", "src/module.py")
        git(self.worktree, "commit", "-m", "after review target")
        path = self.root / ".factory-runtime/review.json"
        atomic_json(path, {"role": "independent-reviewer", "run_id": "fresh-review",
                           "sha": state["target"], "verdict": "approved", "findings": []})
        with self.assertRaisesRegex(ProtocolError, "stale"):
            record_review(self.root, self.task, self.policy, path)
    def test_worker_cannot_review_own_run(self):
        state = self.complete_worker()
        path = self.root / ".factory-runtime/review.json"
        atomic_json(path, {"role": "independent-reviewer", "run_id": state["run_id"],
                           "sha": state["target"], "verdict": "approved", "findings": []})
        with self.assertRaisesRegex(ProtocolError, "fresh"):
            record_review(self.root, self.task, self.policy, path)
    def test_unknown_activity_does_not_accept(self):
        dispatch(self.root, self.task, self.policy, self.ao, "project")
        self.ao.activity = "unknown"
        result = tick(self.root, self.task, self.policy, self.ao)
        self.assertEqual(result["status"], "RUNNING")
        self.assertNotIn(("exit",), self.ao.calls)
    def test_task_deadline_stops_and_escalates(self):
        dispatch(self.root, self.task, self.policy, self.ao, "project")
        receipt_path = self.root / ".factory-runtime/TASK-001.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["started"] = 0
        atomic_json(receipt_path, receipt)
        result = tick(self.root, self.task, self.policy, self.ao)
        self.assertEqual(result["status"], "NEEDS_HUMAN")
        self.assertIn(("exit",), self.ao.calls)
    def test_queue_respects_human_state(self):
        state = initial_state("TASK-001")
        state["status"] = "NEEDS_HUMAN"
        save_state(self.root, state)
        result = queue_step(self.root, self.policy, self.ao, "project")
        self.assertTrue(result["idle"])
        self.assertFalse(self.ao.calls)


if __name__ == "__main__":
    unittest.main()
