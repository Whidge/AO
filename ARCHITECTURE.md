# Boundaries

AO owns sessions, worktrees, provider transports, PR facts and native reviews.
The external factory layer owns Git contracts, bounded prompt construction,
deterministic gates and finite policy transitions. It uses AO's loopback API;
it never opens AO's database or reimplements its worktree/session manager.

`scripts/factorylib/protocol.py` has no AO/network/provider dependency.
`scripts/factorylib/ao.py` translates policy into AO's public HTTP contract.
`scripts/factorylib/github.py` adds a host-side CI/exact-SHA merge gate via gh.
`scripts/factorylib/cli.py` composes commands. Enforce these boundaries with
`scripts/architecture_check`.

Task contracts contain semantics; policy maps capability classes to harness/model.
Task state and checkpoints are Git files; session ids and raw events are ignored
runtime data. Reviews and verification evidence are tied to the exact Git SHA.
