# Execution permissions

Upstream `backend/pkg/agentruntime/command.go` maps **Codex terminal default** to
`--dangerously-bypass-approvals-and-sandbox`. Empty/unset is not a safe cross-agent
permission policy. Our Codex AO adapter rejects default and uses accept-edits,
which maps to on-request. Native chat's default similarly maps to danger-full-access; accept-edits maps
to workspace-write/on-request. Inspect both paths on upgrade. No AO source patch is needed to select safer config.

Cursor auto maps to `--force`; reject it until installed harness containment is
verified. Claude auto uses its native classifier, not an OS sandbox. Neither Git
scope checks nor worktree isolation prevent reading credentials or executing
dangerous code. For unattended production use, run workers AND verification in a
container/VM with project-only writes, controlled egress and no deployment secrets.

The live local Claude benchmark uses default permissions and explicit Read/Edit/
Write plus a short allowlist of factory/test/Git commands. A denied help command
caused the first finite failure; its specific read-only permission was added.
Git add/commit and verification commands remain trusted project operations, not a
security boundary against hostile task content. Live Codex probes use explicit
read-only/workspace-write and never-approval; this refuses operations needing
escalation rather than silently granting full access. Reviewers use read-only tools.

Runtime receipts can contain prompts/code but are ignored by Git and never passed
to another provider. They do not contain auth stores. Metrics exports contain only
aggregate usage, byte counts, ids and hashes. The isolated test daemon binds
127.0.0.1 and explicitly disables telemetry. No system-wide install or provider account switch is performed. The user selected
Whidge/AO for explicit live PR/CI/merge testing; no external repository is created.
GitHub publication includes only curated source, protocol and aggregate reports.

Controller artifact checks now use Bubblewrap by default. The process gets no
network, home directory or provider environment; only /usr, /bin, /lib, /lib64,
temporary /tmp, minimal /dev and /proc, and the worktree are mounted. Shared Git
metadata is mounted read-only. Missing bwrap or namespace permissions fail the
check. Explicit trusted-local is reserved for known trusted test fixtures and is
never selected automatically. A runtime requiring files outside these mounts must
be provisioned deliberately. Workers still need their own OS containment; a safe
verifier cannot contain an unrestricted worker.
