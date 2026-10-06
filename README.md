# Portable engineering protocol on Agent Orchestrator

This is an external, standard-library Python integration with upstream AO.
AO owns sessions and worktrees; Git owns project intent, tasks and checkpoints.
No AO fork or alternate orchestrator is included.

Start with [the audit](docs/audit.md), [the protocol](docs/protocol.md),
[reuse instructions](docs/reuse.md) and [permission findings](docs/security.md).
Measured live outcomes and remaining gaps are in [results](docs/results.md).

```sh
./scripts/verify
./scripts/factory init /absolute/path/to/another/repo
```

The adjacent `../demo` is a real Git benchmark repository registered as
`portable-demo` in the isolated AO daemon. The official packaged CLI lives at
`../.runtime/bin/ao`; the daemon's SQLite/runtime data is at `../.runtime/ao`.
The extracted binary reports `dev`; its release asset provenance is v0.13.3.

```sh
./scripts/ao_local session ls --json
AO_RUN_FILE="$PWD/../.runtime/ao/running.json" ./scripts/factory --root ../demo status
AO_RUN_FILE="$PWD/../.runtime/ao/running.json" ./scripts/factory --root ../demo queue \
  --project portable-demo --steps 120 --interval 5 --local-merge
./scripts/factory --root ../demo status --needs-human
./scripts/factory --root ../demo report TASK-001
```

`queue` is serial and bounded. It dispatches one ready contract, polls AO without
model calls, runs checks, starts a fresh read-only reviewer, and optionally merges
locally in repositories with no remote. It preserves failed clean scoped commits
as seeds for fresh AO workers. It does not replay conversations. Approval handling
is restricted to scoped Git staging/commit and local AO completion reports; other
requests stop as NEEDS_HUMAN. Diagnostics and consequential decisions remain
explicit finite states. Milestone pause uses `--pause-after-milestone NAME`.

Live CLI tests are opt-in and use existing authentication; they consume provider
usage. Never include them in CI's deterministic command:

```sh
./scripts/live_probe --root ../demo --provider codex --role discovery
./scripts/live_probe --root ../demo --provider claude-code --role discovery
./scripts/benchmark_report --root ../demo --output docs/measurements.json
```

For GitHub projects, the optional `remote-step` adapter publishes the exact reviewed
branch, polls required CI without model calls, and uses GitHub's atomic merge SHA
guard. The queue uses it when `publish` is true. Required branch protection is
checked by default, including admin enforcement; no bypass is used. See
[remote setup](docs/reuse.md). `publish` and `auto_merge` default to false.

Cursor/Grok adapters are represented in policy but their local CLIs are absent;
those providers have not passed live portability tests. Production unattended
execution still needs containment and a host plan that supports branch protection.

This checkout is the live GitHub test project `Whidge/AO`. Its explicit policy
publishes and merges reviewed tasks only after required protected CI. The
`examples/slug.py` contract exercises the pipeline with a small real AO worker.
