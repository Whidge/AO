# Portable project protocol v1

Requirements: Git, Python 3.10+, current AO daemon, an authenticated harness.
Linux is the tested platform (controller lock uses flock). No Python packages.

## Canonical files

- `AGENTS.md`: small rules/index; hard cap 6000 UTF-8 bytes.
- `CLAUDE.md`: `@AGENTS.md`, no copied rules. Cursor and Codex use AGENTS.md.
- `.agents/skills`: canonical skill sources. `.claude/skills` is a relative
  symlink; Codex and Cursor discover `.agents/skills` directly. On platforms
  without symlink support generate bridges from source; never edit copies.
- `.project/PROJECT.md`: product direction; create roadmap/milestones/ADRs only
  when planning needs them. No separate PHILOSOPHY duplicating global rules.
- `.project/tasks/TASK-*.json`: immutable worker contract for a dispatched task.
- `.project/state/TASK-*.json`: controller-owned finite task state, Git committed.
- `.project/checkpoints/TASK-*.json`: concise worker-authored continuation facts.
- `.project/proposals/PROPOSAL-*.json`: discovered work, deduplicated by content.
- `.project/policy.json`: routing, finite limits and canonical commands.
- `.factory-runtime`: ignored AO delivery receipts, verification evidence, metrics.

AO's own runtime data stays outside canonical project state. Do not commit raw
transcripts, auth stores, generated full repository summaries or daemon databases.

## Task contract

```json
{
  "id": "TASK-042",
  "title": "Add versioned project export",
  "goal": "Round-trip projects through versioned JSON.",
  "acceptance": ["Round trip preserves project", "Invalid versions fail clearly"],
  "scope": ["src/persistence/*", "tests/test_persistence.py"],
  "references": ["docs/persistence.md"],
  "skills": ["task-implementation"],
  "constraints": ["Domain must not import GUI", "No unrelated refactoring"],
  "verification": [["python3", "-m", "unittest", "tests.test_persistence"]]
}
```

Required: id/title/goal/acceptance/scope/verification. Optional: references, skills,
constraints, depends_on, risk (`cheap|standard|strong`), milestone. No conversation,
provider, model, duplicate status or redundant estimate in a task contract. Scope
uses repository-relative case-sensitive fnmatch patterns; use explicit paths when
possible. Commands are argv arrays, executed without shell interpolation.
Do not let untrusted third parties write verification commands: they are executable
project policy. Validation detects traversal, escaping symlinks, unknown keys,
missing references, duplicate keys and dependency cycles.

## Context and handoff

`factory capsule TASK-ID --base SHA` injects AGENTS.md, one contract, selected skill
paths, reference paths, base SHA and this task's checkpoint. Reference contents,
code and skill bodies are read progressively by the worker. No roadmap/history,
unrelated ADRs, reports or transcripts are injected. A manifest hashes referenced
files. The cap measures injected UTF-8 bytes; bytes/4 is labeled a proxy, not true
tokens. AO system context and subsequent retrieval are separate measurements.

Workers run checks and `factory checkpoint TASK-ID --base SHA --next TEXT`, then
commit scoped work and checkpoint. The checkpoint implementation_sha is the HEAD before checkpoint creation; the
controller records the final artifact SHA separately after commit.
Empty next means implementation is ready for
deterministic evaluation, not acceptance. Before continuing elsewhere, cherry-pick
the checkpoint commit into a clean checkout/worktree or use the same preserved AO
branch with a fresh harness. Give the new harness a fresh capsule; do not resume
the old provider conversation. There is no provider conversation id in canonical
checkpoint data.

## Deterministic lifecycle

1. Commit a validated protocol/contract in a clean control checkout.
2. `factory register --project ID` uses the current loopback daemon, registers
   the repo, and explicitly sets permissions and reviewer harness. Registration
   creates a new project; it does not rewrite another registered project's config.
3. `factory dispatch TASK-ID --project ID [--provider HARNESS]` builds a capsule,
   stores a delivery receipt, commits RUNNING state, and asks AO to create a
   worker/worktree. Dependency tasks must already be MERGED.
4. `factory tick TASK-ID` observes AO deterministically. Unknown/lost activity is
   not success. A committed completion checkpoint + idle worker is required.
   The controller exits the agent, checks scope, runs trusted targeted + full
   validation, then commits REVIEWING or a finite failure state.
5. Generate a review capsule in the worker checkout using exact base/HEAD and
   controller evidence. Use a fresh read-only reviewer invocation, or AO's native
   SHA-bound reviewer when a PR exists. No transcript is supplied. For local
   review record `{role:"independent-reviewer",run_id,sha,verdict,findings}` using
   `factory review-record TASK-ID FILE` in the control checkout.
6. Local no-remote test repos can use `factory merge-local TASK-ID`; it verifies
   unchanged reviewed head, merges, reruns full verification and commits MERGED.
   Remote repos use PRs/branch protection. The local merge helper refuses remotes.

Dispatch and state mutations hold a repository lock. Dispatch records a unique
branch and receipt before network I/O. `factory recover TASK-ID` looks up the
original AO session by project/branch/name; it NEVER resends an uncertain spawn.
The packaged release ignored clientRequestId and duplicated workers during our
initial replay test; source-main support must not be assumed for a release.
Missing/ambiguous handles stop at NEEDS_HUMAN. Both the failure and corrected live
lookup results are recorded. Failed clean scoped commits can seed a new unique
branch; AO creates the worktree. Dirty/scope-violating branches remain preserved
and are not automatically reused. No forced worktree deletion or conversation replay.

Remote GitHub projects can use `factory remote-step TASK-ID`. Policy must explicitly
permit publication. It checks unchanged reviewed SHA, publishes the branch and
records a PR link, then observes required check-runs/statuses at that head. With
`auto_merge` enabled it merges using GitHub's atomic `sha` parameter. Required host
protection is the default, including strict required checks and admin enforcement.
Without it the task becomes NEEDS_HUMAN. An explicit `require_protection:false`
policy is an exception for a user-approved test, not equivalent to host protection.
The controller fetches the merged base, integrates it locally, verifies and records
MERGED. Controller-owned task-state commits may accompany the next task PR;
workers cannot edit those files. GitHub/AO remain the owners of PR/CI facts.

## Failure and escalation

READY → RUNNING → VERIFYING → REVIEWING → ACCEPTED → MERGED.
After failed execution/checks/review: READY until `attempts` is reached, then
DIAGNOSING for one independent diagnostic pass. `diagnostic-complete --reason`
permits one recovery attempt; exhausted diagnostics leads to NEEDS_HUMAN.
The queue schedules one fresh read-only diagnostic invocation using the strong
route, never a panel of reviewers. NEEDS_HUMAN stops automatic dispatch; `resume --reason` acknowledges a
human decision and starts a fresh bounded retry cycle.

Immediately escalate consequential contradictions, security/destructive decisions,
missing credentials/permissions, unsupported hard budgets, task deadline, or lost
delivery handles. Ordinary implementation choices stay with the worker. Commands
do not authorize modifying a task to bypass a failing acceptance criterion.

## Routing, budgets and measurement

Edit policy routing classes; model ids are optional configuration, never project
semantics. Explicit provider override preserves hard budgets and clears incompatible
model/effort. The adapter registry is transport-specific: current AO spawn accepts
model/effort and permissions, but not hard max cost/turns/tokens. These requirements
fail before launch. Codex CLI supports effort and usage JSON; Claude print supports
max-budget-usd and max-turns (live probe required). Cursor/Grok CLI controls remain
unknown until installed/probed. Native subagents are not required by this workflow.

`factory report TASK-ID [--events JSONL --harness codex|claude-code]` generates compact
machine data. Null usage means unavailable, never zero. Counts from terminal usage
events are normalized; AO native conversation totals are not guessed from the CLI
events. Review usage is separate. Runtime reports can be curated into Git reports
without committing raw provider conversations. Compare injected context separately
from billed usage; a small prompt alone does not prove lower total cost.

## Security and remote operation

AO worktrees isolate Git changes, not OS access. Set explicit permissions (the AO
default can resolve to auto). Codex workspace-write is a real harness sandbox;
Claude's allowed tools/auto classifier are not OS containment. Use a container/VM
and controlled egress for unattended Claude/Cursor/Grok where native isolation is
unavailable. Never use bypass flags. Keep auth outside repo/logs, restrict project
shell tools, and gate destructive commands externally. Controller verification defaults to Bubblewrap: no network or home mounts,
only system runtimes and the worktree, with shared Git metadata read-only. It never
falls back to host execution if isolation is unavailable. Install bwrap or stop for
human setup. Explicit `verification_isolation:"trusted-local"` is for trusted
fixtures only. Reviewers inspect evidence without executing untrusted PR code.
The canonical developer `scripts/verify` remains a normal local/CI command; the
controller wraps it. Workers need their own containment as well.

Native AO review currently posts provider review comments and records its verdict
as COMMENT (same-author accounts cannot APPROVE their own PR). Protect merges with
the exact head, required CI and policy approval; `publish`/`auto_merge` are false by
default and are not silently enabled by the local helpers. AO owns PR maintenance;
remote merge needs a configured repository, permissions and passing exact-head checks.
