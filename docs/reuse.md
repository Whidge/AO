# Reuse on another repository

From this checkout:

```sh
./scripts/factory init /absolute/path/to/repo
```

Existing files are preserved. Read the returned preserved list and adapt the
project's existing AGENTS.md, architecture, verifier and CI rather than replacing
them. Init does not duplicate existing rules or overwrite verification. Add the
Git-ignore runtime entries. The stock verifier is for this integration repository:
replace it with the actual project's lint/type/build/tests/architecture checks.
Keep it an executable `scripts/verify`. Set policy verification to that command.

Commit `.claude/skills` as a relative symlink and use `CLAUDE.md`'s @AGENTS.md import.
Cursor/Codex need no extra rule copies. A Grok model selected through Cursor uses
the Cursor harness conventions; a standalone Grok harness requires its own probe.

Write PROJECT.md and initial task contracts. Add architecture checks appropriate
to your language (e.g. Python AST forbidden imports or dependency-cruiser for JS).
Do not copy the integration's architecture boundaries into your application.
Use explicit scope paths and cheap task checks. Avoid duplicating full-suite checks
inside every contract; the controller runs the canonical checks afterwards.

Install Bubblewrap for isolated controller verification (Linux). Tests have no
network/home access; only system runtimes and the checkout are mounted. Projects
requiring custom runtimes need a reviewed containment setup. Do not switch to
trusted-local as an automatic fallback.

Choose provider/model mappings in `.project/policy.json`, finite attempt/time
limits and explicit permissions. Unsupported hard controls cause an error. Keep
secrets outside Git. Use AO desktop or CLI to start a daemon; the commands locate
it through `AO_RUN_FILE` or `~/.ao/running.json`:

```sh
./scripts/factory validate
git add AGENTS.md CLAUDE.md .agents .claude .project scripts docs .gitignore
git commit -m 'chore: add portable AO protocol'
./scripts/factory register --project my-project
./scripts/factory dispatch TASK-001 --project my-project --provider codex
./scripts/factory status --needs-human
./scripts/factory tick TASK-001
./scripts/factory report TASK-001
```

For continuous operation use the bounded queue driver described in README. Start
with serial tasks. Pin your tested AO build and harness versions; validate the
loopback contract after upgrades. Keep projects' prompts small and measure native
system overhead and billed provider usage separately.

For a product idea invoke task-planning with PROJECT.md and relevant architecture.
Resolve consequential ambiguity, then create dependency-ordered tasks. No generic
planner can infer missing product decisions reliably; record those decisions in
Git. Continuous requirements update only affected vision/task/proposal/ADR files.

For GitHub operation, set origin to the intended repository and configure:

```json
"publish": true,
"auto_merge": true,
"github": {
  "repo": "OWNER/REPO",
  "base": "main",
  "required_checks": ["verify"],
  "require_protection": true
}
```

These are top-level fields in policy.json. Authenticate `gh` outside the repository.
Install your actual `scripts/verify` in a GitHub Actions workflow named `verify`.
Configure main to require `verify`, up-to-date branches and admin enforcement.
Set required human/app approvals in GitHub if your organization requires them;
the adapter never bypasses them. A reviewer using the author's token cannot
provide GitHub's formal independent APPROVE, so that policy needs another account
or app. Test native AO SCM discovery after registering the project.

The serial queue now handles accepted tasks through publishing, CI and exact-SHA
merge when explicitly enabled. `remote-step TASK-ID` runs one bounded observation.
Private free-plan repositories may not support protected branches: GitHub returns
403. Preserve privacy and leave the PR open unless the user explicitly chooses
an alternative. `require_protection:false` still enforces the local CI/SHA gate,
but other writers can bypass it; it must not be described as host protection.

This GitHub adapter uses the installed gh CLI and the same host facts tracked by
AO. GitLab automation is left to native AO until an equivalent adapter is tested.
