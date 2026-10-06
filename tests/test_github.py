"""Merge gates use actual PR facts; fixture transport isolates external mutations."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factorylib.github import GitHub
from factorylib.protocol import ProtocolError


class Host(GitHub):
    def __init__(self):
        super().__init__(Path.cwd(), {"repo": "owner/repo"})
        self.pr = {"state": "open", "draft": False, "mergeable": True,
                   "head": {"sha": "a" * 40, "ref": "factory/task", "repo": {"full_name": "owner/repo"}},
                   "base": {"ref": "main"}, "html_url": "https://github.com/owner/repo/pull/1"}
        self.protection = {"required_status_checks": {"strict": True, "contexts": ["verify"]}, "enforce_admins": {"enabled": True}}
        self.runs = [{"name": "verify", "status": "completed", "conclusion": "success"}]
        self.calls = []
    def api(self, path, method="GET", data=None):
        self.calls.append((path, method, data))
        if path == "pulls/1":
            return copy.deepcopy(self.pr)
        if path == "branches/main/protection":
            return self.protection
        if "check-runs" in path:
            return {"total_count": len(self.runs), "check_runs": self.runs}
        if "/status?" in path:
            return {"total_count": 0, "statuses": []}
        if path == "pulls/1/merge":
            return {"merged": True, "sha": "b" * 40}
        raise AssertionError(path)


class MergeGateTest(unittest.TestCase):
    def setUp(self):
        self.host = Host()
    def merge(self):
        return self.host.merge(1, "a" * 40, "factory/task")
    def test_server_merge_is_guarded_by_reviewed_sha(self):
        self.assertTrue(self.merge()["merged"])
        self.assertEqual(self.host.calls[-1], ("pulls/1/merge", "PUT", {"sha": "a" * 40, "merge_method": "merge"}))
    def test_changed_head_never_merges(self):
        self.host.pr["head"]["sha"] = "c" * 40
        with self.assertRaisesRegex(ProtocolError, "changed"):
            self.merge()
        self.assertFalse(any(method == "PUT" for _, method, _ in self.host.calls))
    def test_fork_or_changed_base_never_merges(self):
        self.host.pr["head"]["repo"]["full_name"] = "untrusted/repo"
        with self.assertRaises(ProtocolError):
            self.merge()
    def test_missing_ci_waits(self):
        self.host.runs = []
        self.assertFalse(self.merge()["ready"])
    def test_pending_pr_check_prevents_green_push_check_merge(self):
        self.host.runs += [{"name": "verify", "status": "in_progress", "conclusion": None}]
        self.assertFalse(self.merge()["ready"])
    def test_failed_ci_never_merges(self):
        self.host.runs[0]["conclusion"] = "failure"
        with self.assertRaisesRegex(ProtocolError, "failed"):
            self.merge()
    def test_insufficient_host_protection_never_merges(self):
        self.host.protection["enforce_admins"]["enabled"] = False
        with self.assertRaisesRegex(ProtocolError, "admin protection"):
            self.merge()
    def test_explicit_unprotected_policy_still_requires_ci_and_sha(self):
        self.host.require_protection = False
        self.assertTrue(self.merge()["merged"])
        self.assertFalse(any("protection" in path for path, _, _ in self.host.calls))
    def test_already_merged_is_recovered_without_second_put(self):
        self.host.pr.update(merged=True, merge_commit_sha="b" * 40)
        self.assertTrue(self.merge()["merged"])
        self.assertFalse(any(method == "PUT" for _, method, _ in self.host.calls))
    def test_unknown_mergeability_waits(self):
        self.host.pr["mergeable"] = None
        self.assertFalse(self.merge()["ready"])
    def test_conflict_never_merges(self):
        self.host.pr["mergeable"] = False
        with self.assertRaisesRegex(ProtocolError, "conflicts"):
            self.merge()


if __name__ == "__main__":
    unittest.main()
