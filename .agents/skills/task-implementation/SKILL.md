---
name: task-implementation
description: Implement an assigned factory task contract and produce a verified Git checkpoint.
---

Use the contract in the capsule. Inspect only named references and relevant code.
Use task verification for iteration; run the canonical verify command at completion.
Use `./scripts/factory scope TASK-ID --base BASE-SHA` before checkpointing.
Write unrelated findings with `./scripts/factory propose --task TASK-ID --reason TEXT
--evidence TEXT --priority normal --files PATH`. Do not widen the task.
Use `./scripts/factory checkpoint TASK-ID --base BASE-SHA --next TEXT` to record
remaining work and test status; then commit scoped files and the checkpoint.
Use a simple `git commit -m TEXT` command; avoid shell chains and heredocs.
For completed work use an empty next value. Do not mark your own work reviewed.
