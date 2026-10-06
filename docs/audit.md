# AO audit — 2026-10-06

Source: https://github.com/OrchestratorInc/agent-orchestrator at
`22542788e705e2031a4fce7085fbe173d118e073`. Test distribution: official Linux
desktop package v0.13.3, extracted locally. No upstream changes are planned.

## Findings

AO main is a Go daemon/CLI with SQLite and an Electron supervisor. Do not use
the old TypeScript plugin API or `agent-orchestrator.yaml` examples. The frozen
npm on-ramp is not the current configuration surface. Source development needs
Go 1.27.1 and Node 24; neither is available here (Node 22.23.2, no Go). Use the
packaged daemon, without rebuilding the GUI.

| Requirement | Class | Evidence / owner |
|---|---|---|
| Isolated worker branches/worktrees | A | session manager + workspace adapter |
| Harness/model per task | A | `ao spawn --agent --model --prompt` |
| Minimal worker prompt | B | native spawn prompt; our deterministic capsule builder |
| Project/role rules and permissions | B | `ao project set-config`, typed ProjectConfig |
| Broad project orchestrator | A/B | native project session; instruct it to persist decisions in Git |
| Durable project truth | B | Git task contracts/docs; AO SQLite remains operational state |
| Provider switching | A/B | native switch-agent/handoff exists; Git checkpoints avoid its conversation dependence |
| Independent artifact review | A/B | native reviewer keyed to target SHA; bounded project review instructions |
| PR/CI/review feedback | A | SCM facts, lifecycle/reaper, review service |
| Provider capabilities | A/C | AO ChatCapabilities; small policy registry for budgets not in ProjectConfig |
| Model capability classes | C | external routing mapping to native harness/model fields |
| Context accounting | A/C | spawn promptBytes/systemPromptBytes; policy bytes + hashes |
| Provider usage | A/C | native ChatUsage input/output/cached; normalize supplied JSONL only when present |
| Max cost/turns/tokens | C/D | harness dependent; native spawn config lacks uniform budget controls; fail on unsupported hard limits |
| Deterministic verification / scope | B/C | repository commands + Git diff, not agent discussion |
| Retry limits / NEEDS_HUMAN / task queue | C | small external policy state; do not replace AO sessions/workspaces |
| Skills | B | `.agents/skills`; Claude bridge only |
| Planning from product idea | B | optional planning skill; human approves consequential ambiguity |
| Continuous requirements / proposals | B/C | append contracts/proposals, preserve unrelated documents |
| Merge authorization / branch protection | E | GitHub/GitLab protections and required checks |
| OS sandbox / credentials | E | harness permissions and OS containment, not prompt instructions |

### Code inspected

`backend/internal/cli/{spawn,project,session,session_switch,review,automation}.go`,
`backend/internal/domain/{projectconfig,agentconfig}.go`,
`backend/internal/ports/{agent,chat}.go`, `backend/internal/session_manager/prompt.go`,
`backend/internal/review/prompt.go`, built-in codex/claude/cursor/grok adapters,
`docs/{architecture,development,STATUS}.md`, and agent setup docs in the frontend.

Native Chat uses Codex app-server or ACP for other compatible harnesses. AO stores
conversation identities and operational checkpoints. These are useful for live
supervision but must not be prerequisites for task recovery. Native switching
includes a source-authored handoff; our portable handoff is a committed task
checkpoint and fresh bounded prompt. Do not forward full transcripts.

### Local harnesses

| Harness | Found | Instruction / skills strategy | Permission / budget observations |
|---|---|---|---|
| Codex 0.160.1 | yes | AGENTS.md; `.agents/skills` | workspace-write/read-only; ephemeral + JSON events; effort via config; no advertised hard max cost/turns |
| Claude Code 2.1.278 | yes | CLAUDE.md `@AGENTS.md`; `.claude/skills` alias | default/acceptEdits/auto/plan; allowedTools; print JSON; max-budget-usd; effort; probe hidden max-turns before use |
| Cursor CLI | no | native AGENTS.md + `.agents/skills` per current docs | cannot verify local flags, model catalog, auth or sandbox |
| Grok standalone / via Cursor | no | Cursor rules apply to Grok selected inside Cursor; standalone support must be separately probed | cannot claim portability from model availability alone |

Availability is not authenticated execution. Live results belong in
`docs/results.md`, including failed/skipped runs; do not manufacture passes.

Primary provider references:

- https://learn.chatgpt.com/docs/agent-configuration/agents-md
- https://learn.chatgpt.com/docs/build-skills
- https://learn.chatgpt.com/docs/security
- https://code.claude.com/docs/en/memory
- https://code.claude.com/docs/en/skills
- https://code.claude.com/docs/en/cli-reference
- https://cursor.com/docs/skills
- https://cursor.com/docs/context/rules

## Incremental implementation plan

1. Establish a dependency-free portable Python CLI and tiny Git protocol. JSON
   task contracts avoid a YAML dependency and ambiguous implicit typing.
2. Validate contracts, bounded prompts, routing, scope, checkpoints and
   deterministic commands against temporary real Git repositories.
3. Register a controlled test repo with the packaged AO daemon. Use its native
   API/CLI for worker creation and worktree ownership. Pin the tested version.
4. Probe installed harnesses with short safe tasks; verify artifacts and skills,
   then attempt Git-only Claude→Codex handoff and fresh review.
5. Add bounded deterministic continuation/recovery only once lower layers pass.
   Unsupported budgets and lost/ambiguous AO signals fail closed.
6. Produce programmatic context/usage reports and truthful portability results.

Avoid generic duplicated testing/debugging/architecture skill packs. Start with
implementation, review, and planning procedures only; project-specific tools
own actual architecture and test rules. No empty milestone/debt/report hierarchy.
