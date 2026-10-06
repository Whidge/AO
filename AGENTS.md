# Project rules

Git is the source of truth. Provider conversations and AO runtime data are disposable.
Start with the assigned `.project/tasks/TASK-*.json`, then read only its references,
selected skills and relevant code. Do not load the roadmap for implementation.

Implement one task. Stay inside its scope. Record unrelated findings as proposals
with `./scripts/factory propose`; do not silently fix them. Do not change contracts,
policy, CI or verification tools to make your own task pass.

Run task verification during iteration and `./scripts/verify` before completion.
Verification, scope checks and a fresh review of the exact commit are required.
The reviewer receives criteria, Git diff and check results, never worker transcripts.

Before handoff, commit scoped changes and a concise `.project/checkpoints/TASK-*.json`
using `./scripts/factory checkpoint`. A fresh agent continues from Git state.
Credentials stay outside Git. Use harness sandbox/approval controls; never enable
bypass permissions. Escalate consequential ambiguity, security/destructive actions,
missing external permissions and exhausted retry/budget limits as NEEDS_HUMAN.

Protocol and operational commands: `docs/protocol.md`. Architecture: `ARCHITECTURE.md`.
Planning truth: `.project/PROJECT.md`; policy: `.project/policy.json`.
