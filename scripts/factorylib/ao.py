"""Thin client of AO's public daemon contract. No database or workspace ownership."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from .protocol import ProtocolError, read_json


class AO:
    def __init__(self, run_file=None):
        path = run_file or os.environ.get("AO_RUN_FILE") or str(Path.home() / ".ao/running.json")
        info = read_json(path)
        port = info.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ProtocolError("invalid AO run file")
        self.base = f"http://127.0.0.1:{port}/api/v1/"

    def request(self, method, path, body=None):
        if path.startswith("/") or ":" in path or ".." in path:
            raise ProtocolError("invalid AO route")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        # Never inherit a remote proxy for the unauthenticated loopback API.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=120) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ProtocolError("AO response exceeds bound")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read(16000))
                error = detail.get("error", detail)
                code = error.get("code", str(exc.code)) if isinstance(error, dict) else detail.get("code", str(exc.code))
                message = error.get("message", "AO request failed") if isinstance(error, dict) else detail.get("message", str(error))
            except (ValueError, AttributeError):
                code, message = exc.code, "AO request failed"
            raise ProtocolError(f"AO {code}: {message}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProtocolError(f"AO transport unavailable; recover by session lookup, never resend an uncertain spawn: {type(exc).__name__}") from exc

    def spawn(self, project, task_id, route, capsule, policy, request_id, branch=None):
        request = self.spawn_request(project, task_id, route, capsule, policy, request_id, branch)
        return self.request("POST", "sessions", request)

    @staticmethod
    def spawn_request(project, task_id, route, capsule, policy, request_id, branch=None):
        result = {"projectId": project, "kind": "worker", "harness": route["harness"],
                  "mode": policy["ao"]["mode"], "approvalMode": policy["ao"]["permission"],
                  "displayName": task_id, "prompt": capsule["text"], "clientRequestId": request_id}
        for key in ("model", "effort"):
            if key in route:
                result[key] = route[key]
        if branch:
            result["branch"] = branch
        return result

    def session(self, session_id):
        return self.request("GET", "sessions/" + urllib.parse.quote(session_id, safe=""))["session"]

    def find_session(self, project, branch, display_name):
        result = self.request("GET", "sessions?includeTerminated=true")
        matches = [s for s in result.get("sessions", []) if s.get("projectId") == project and
                   s.get("branch") == branch and s.get("displayName") == display_name]
        if len(matches) > 1:
            raise ProtocolError("ambiguous AO session recovery")
        return matches[0] if matches else None

    def workspace(self, session_id):
        result = self.request("GET", "desktop/sessions/" + urllib.parse.quote(session_id, safe="") + "/workspace")
        return Path(result["workspacePath"])

    def exit_agent(self, session_id):
        return self.request("POST", "sessions/" + urllib.parse.quote(session_id, safe="") + "/exit-agent", {})

    def usage(self, session_id):
        return self.request("GET", "usage/sessions/" + urllib.parse.quote(session_id, safe=""))

    def pending_approvals(self, session_id):
        # A pending approval may precede the last commentary event. Fetch a bounded
        # page, parse activities locally, and never inject messages into model context.
        result = self.request("GET", "sessions/" + urllib.parse.quote(session_id, safe="") + "/conversation?limit=64")
        return [a for a in result.get("activities", []) if a.get("activityKind") == "approval" and a.get("status") == "pending"]

    def approve_once(self, session_id, approval):
        offered = [d for d in approval.get("detail", {}).get("decisions", []) if d.get("kind") == "allow_once"]
        if len(offered) != 1:
            raise ProtocolError("no unambiguous one-time approval offered")
        path = "sessions/" + urllib.parse.quote(session_id, safe="") + "/conversation/approvals/" + urllib.parse.quote(approval["requestId"], safe="") + "/resolve"
        return self.request("POST", path, {"decisionId": offered[0]["id"]})

    def review(self, session_id, route):
        request = {"harness": route["harness"], "source": "manual", "enableAutoInject": False}
        controls = {k: route[k] for k in ("model", "effort") if k in route}
        if controls:
            request["agentConfig"] = controls
        return self.request("POST", "sessions/" + urllib.parse.quote(session_id, safe="") + "/review/trigger", request)
