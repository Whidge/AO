# Evaluation results — 2026-10-06

## Completed local evidence

- Upstream AO source and packaged Linux release audited before implementation;
  source remains clean at 22542788e705e2031a4fce7085fbe173d118e073. **No AO source
  modifications or fork.** See audit.md and ao-provenance.json.
- External protocol passes 59 deterministic tests using real temporary Git
  repositories, branches/worktrees and subprocess verification. Transport fixtures
  cover failed delivery, unknown signals and GitHub gates without external writes.
- Codex and Claude both discovered canonical rules, the task and implementation
  skill in fresh authenticated CLI invocations. Cursor/Grok are absent: skipped,
  not passed. Cursor compatibility is documentation-backed, not live evidence.
- Claude started TASK-001, committed its implementation and next-action checkpoint;
  a fresh AO Codex worker completed it from Git without any conversation transfer.
  A fresh read-only Codex reviewer approved the exact verified commit; local merge
  and post-merge validation passed.
- AO Codex completed TASK-002 through the bounded queue, controller verification,
  independent artifact review and local merge. Both tasks are durably MERGED.
  Task 1 finished with 7 acceptance tests; task 2 with 12.
- Equivalent TASK-002 from the same base ran under Claude in a separate checkout:
  first attempt exhausted 12 turns, second succeeded with a clean scoped commit
  and 15 passing tests. Fresh Codex review approved ade3268e2323a275bbd97b4b003a367850186e1b.
  See claude-portability-measurements.json and claude-portability-review.json.
- Daemon restart preserved Git task state and AO sessions. Initial spawn replay
  FAILED: v0.13.3 ignored clientRequestId and created duplicate sessions, whose
  agent contexts were stopped. Recovery was changed to unique-branch session
  lookup without POST. Both original live sessions were found after restart with
  zero new spawn requests. See restart-results.json and recovery-lookup-results.json.

## Measurements and limits

Worker capsules were 2,645 and 2,474 UTF-8 bytes. AO added 12,517 system bytes to
both, before harness rules and subsequent tool reads. Input totals remained
333,911 and 337,836 tokens (most cached); small injection alone does not establish
low total cost. AO's cost estimates were approximately $0.09358 and $0.13244;
these are estimates, not invoices. Codex CLI billed cost is unavailable (null).

One paired artifact review used the same configured Codex default and target:
bounded context 10,919 bytes versus 90,417 bytes for a repository dump (**87.9%
less injection**). Input tokens were 54,675 versus 72,821 (**24.9% fewer**), and both
approved. Durations were 12.384 versus 9.726 seconds. Cache/order and randomness
were uncontrolled; this does not prove general cost, speed or reliability gains.
See review-comparison.json. measurements.json includes worker and reviewer usage.
Claude cache creation/read tokens are separate; raw uncached input alone must not
be compared with Codex total input. All failed attempts are retained in aggregates.

These are small controlled tasks, not a weeks-long reliability benchmark. Early
runs required adapter fixes for allowlisted help, scoped Git/AO reporting and
pending-approval pagination; those failures must not be counted as unattended
success. The final queue operates serially and requires no LLM status checker.

## Remote test

The first selected repository was private and had no branch-protection entitlement.
After an inherited Git author issue, the user deleted it and recreated Whidge/AO
as public. Authentication was verified as Whidge; all published commits use Whidge
with 174260058+Whidge@users.noreply.github.com. The old private repository received
only its initial baseline before deletion, not integration code or transcripts.

Public main now requires `verify`, up-to-date branches and admin enforcement;
force pushes/deletion are disabled. A real fresh AO Codex worker completed the
example task, passed isolated full verification and a fresh artifact review, then
PR #1 passed branch and PR CI and merged through the atomic exact-SHA gate. Post-
merge local validation passed. Its main CI then exposed a fixture cleanup race
(background Git maintenance); temporary fixture repos now disable that maintenance.
The final queue additionally waits for merged-head CI before continuing.

Independent integration reviews found and verified fixes for staged-index scope,
inherited retry checkpoint handling, public CLI recovery without AO/run-file, and
persistent verifier cache writes. The final review approved commit d60de6a4279868a41d91f18a3108a2bb72f2ba84.
Verification now mounts the checkout and Git metadata read-only, with no home or
network and an ephemeral Python cache. Boundary probes and both demo contracts
pass; see verification-boundaries.json. Build output must go to /tmp or a reviewed
disposable container setup. Test details and failed reviews remain recorded.

The second protected task also completed and merged (PR #2), delivering the final
fixes. Branch CI, PR CI and merged-main CI passed; the queue observed merged-head
CI before committing MERGED. Both remote tasks are MERGED with no NEEDS_HUMAN tasks.
See remote-results.json. This verifies automatic continuation across two tasks,
not weeks-long reliability.

Native AO SCM initially returned SCM_UNAVAILABLE because the system GitHub CLI
lacks `gh auth token`. Installing the checksum-verified official gh v2.102.0 only
in the isolated runtime resolved authentication, using the existing login without
copying credentials. A closed-PR claim then correctly returned PR_NOT_OPEN.
See github-cli-provenance.json; native open-PR attachment will be checked while
publishing final results. The GitHub adapter's live gates are independently proven.

## Remaining boundaries

- Hard budget controls vary by harness; AO spawn exposes no uniform cost/turn/token
  caps. Unsupported hard policies fail before dispatch; polling thresholds can
  overshoot and are not hard limits. Claude CLI cost/turn caps were tested.
- Bubblewrap-isolated verification passed on both completed demo contracts;
  see isolated-verification.json. Staged scope, verification restart and inherited
  checkpoint review findings now have regression tests.
- Native defaults can bypass Codex isolation; explicit accept-edits is required.
  Claude tool permissions are not OS containment. Container/VM deployment and
  controlled credentials remain prerequisites for unattended production use.
- Native AO reviewers post COMMENT with the author's token. Formal host approvals
  require a separate reviewer account/app when branch policy requires them.
- Cursor/Grok live tests need those installed/authenticated harnesses. No claim is
  made about their current permission/budget flags. GitLab remote automation is
  not live-tested here.
- Planning uses the native AO project orchestrator plus the planning skill. Product
  ambiguities and major decisions still require human input; no specific product
  was supplied to benchmark complete idea-to-application generation.
- Git contracts survive runtime loss; uncertain delivery without a receipt or a
  unique AO session stops safely. Native transcripts are never canonical memory.
