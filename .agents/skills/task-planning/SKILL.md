---
name: task-planning
description: Translate product requirements or changes into small dependency-ordered factory task contracts.
---

Read PROJECT.md and only the relevant planning/architecture sections. Classify
incoming requirements as product direction, milestone, task/bug, proposal or ADR.
Update only affected state; create milestones/ADRs when decisions warrant them.
Write atomic JSON contracts following docs/protocol.md. Include explicit acceptance,
allowed paths, references and argv verification commands. Add dependencies when
sequencing requires them. Validate with `./scripts/factory validate`.
Persist important decisions in Git. Escalate contradictory requirements or major
tradeoffs with options and consequences. Route workers through factory dispatch;
give them one capsule and no planning conversation. Do not create extra managers.
