"""Small GitHub merge gate. GitHub/AO own PR and CI facts; no cached SCM database."""
import json
import re
import subprocess

from .protocol import ProtocolError, resolve_sha


class GitHub:
    def __init__(self, root, config):
        self.root = root
        self.repo = config.get("repo", "")
        self.base = config.get("base", "main")
        self.required = config.get("required_checks", ["verify"])
        self.require_protection = config.get("require_protection", True)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo):
            raise ProtocolError("configure github.repo as owner/repo")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", self.base) or ".." in self.base:
            raise ProtocolError("invalid GitHub base branch")
        if type(self.require_protection) is not bool or not isinstance(self.required, list) or not self.required or any(not isinstance(n, str) or not n for n in self.required):
            raise ProtocolError("explicit GitHub required_checks/protection policy required")

    def api(self, path, method="GET", data=None):
        argv = ["gh", "api", f"repos/{self.repo}/{path}", "--method", method]
        if data is not None:
            argv += ["--input", "-"]
        try:
            result = subprocess.run(argv, cwd=self.root, input=json.dumps(data) if data is not None else None,
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProtocolError(f"GitHub unavailable: {type(exc).__name__}") from exc
        if result.returncode:
            # gh debug logs are intentionally not exported: return only API message.
            try:
                message = json.loads(result.stdout).get("message", "GitHub request failed")
            except ValueError:
                message = "GitHub request failed; inspect authentication/network"
            raise ProtocolError(message)
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise ProtocolError("invalid GitHub response") from exc

    def publish(self, branch, target, title, body):
        if not branch.startswith("factory/") or resolve_sha(self.root, branch) != target:
            raise ProtocolError("only the unchanged reviewed factory branch may be published")
        remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=self.root, capture_output=True, text=True)
        urls = {f"https://github.com/{self.repo}.git", f"https://github.com/{self.repo}", f"git@github.com:{self.repo}.git"}
        if remote.stdout.strip() not in urls:
            raise ProtocolError("origin does not match explicitly configured GitHub repository")
        # Query first for recovery after an uncertain create response; never make a
        # second PR or force push over a changed artifact.
        owner = self.repo.split("/")[0]
        pulls = self.api(f"pulls?state=all&head={owner}:{branch}&base={self.base}&per_page=100")
        if len(pulls) > 1:
            raise ProtocolError("ambiguous existing PR")
        if pulls:
            if pulls[0]["head"]["sha"] != target:
                raise ProtocolError("published PR head changed after review")
            return pulls[0]
        result = subprocess.run(["git", "push", "origin", f"{target}:refs/heads/{branch}"], cwd=self.root,
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ProtocolError("Git push failed; no force push attempted")
        return self.api("pulls", "POST", {"title": title, "head": branch, "base": self.base, "body": body})

    def gate(self, number, target, branch):
        if type(number) is not int or number < 1:
            raise ProtocolError("invalid PR number")
        pr = self.api(f"pulls/{number}")
        if pr["head"]["sha"] != target or pr["head"]["ref"] != branch or pr["head"]["repo"]["full_name"] != self.repo or pr["base"]["ref"] != self.base:
            raise ProtocolError("PR head/base changed after review")
        if pr.get("merged"):
            return {"merged": True, "merge_sha": pr["merge_commit_sha"], "url": pr["html_url"]}
        if pr["state"] != "open" or pr.get("draft"):
            raise ProtocolError("PR is closed or draft")
        if self.require_protection:
            protection = self.api(f"branches/{self.base}/protection")
            status = protection.get("required_status_checks") or {}
            protected_checks = set(status.get("contexts", [])) | {c["context"] for c in status.get("checks", [])}
            if not set(self.required) <= protected_checks or not status.get("strict") or not (protection.get("enforce_admins") or {}).get("enabled"):
                raise ProtocolError("base must enforce required CI, up-to-date branches and admin protection")
        if pr.get("mergeable") is False:
            raise ProtocolError("PR conflicts with base; fresh verification/review required")
        if pr.get("mergeable") is None:
            return {"ready": False, "reason": "GitHub computing mergeability"}
        checks = self.checks(target)
        return {**checks, "url": pr["html_url"]}

    def checks(self, target):
        # Both check-runs and legacy statuses are supported. All matching runs must
        # finish successfully, including push and PR runs on this exact head.
        runs = self.api(f"commits/{target}/check-runs?per_page=100")
        statuses = self.api(f"commits/{target}/status?per_page=100")
        if runs.get("total_count", 0) > 100 or statuses.get("total_count", 0) > 100:
            raise ProtocolError("CI response exceeds bounded page; inspect manually")
        observed = {}
        for item in runs.get("check_runs", []):
            observed.setdefault(item["name"], []).append("success" if item["status"] == "completed" and item["conclusion"] == "success" else "pending" if item["status"] != "completed" else "failure")
        # The combined status endpoint returns latest status per context.
        for item in statuses.get("statuses", []):
            observed.setdefault(item["context"], []).append(item["state"])
        for name in self.required:
            values = observed.get(name, [])
            if any(v in ("failure", "error") for v in values):
                raise ProtocolError(f"required CI failed: {name}")
            if not values or any(v != "success" for v in values):
                return {"ready": False, "reason": f"await required CI: {name}"}
        return {"ready": True, "sha": target, "checks": self.required}

    def merge(self, number, target, branch):
        gate = self.gate(number, target, branch)
        if gate.get("merged") or not gate.get("ready"):
            return gate
        result = self.api(f"pulls/{number}/merge", "PUT", {"sha": target, "merge_method": "merge"})
        if not result.get("merged"):
            raise ProtocolError("GitHub refused merge; no bypass attempted")
        return {"merged": True, "merge_sha": result["sha"], "url": gate["url"], "checks": gate["checks"]}
