# Batch trio (atomicity / create / reconciler) — design feedback

**Status:** feedback (review of WIP design docs)
**Date:** 2026-04-29
**Reviewer:** Claude Opus 4.7 (1M context), at user's request
**Scope:** the three sibling specs dated 2026-04-29 in this directory:
- A — [`2026-04-29-batch-atomicity-refactor-design.md`](2026-04-29-batch-atomicity-refactor-design.md)
- B — [`2026-04-29-batch-create-variant-design.md`](2026-04-29-batch-create-variant-design.md)
- C — [`2026-04-29-orphan-reconciler-design.md`](2026-04-29-orphan-reconciler-design.md)

This document records loopholes, security/usability flaws, performance
issues, error-handling gaps, invalid-structure risks, and work-item-type
coverage gaps found during review. Each finding cites the spec section
(or, where relevant, the live code that contradicts a claim).

---

## Net

Two findings (1, 2) genuinely block. They invalidate the spec's headline
atomicity guarantee (Spec A) and the reconciler's headline safety claim
(Spec C) respectively, and neither is covered by the existing open
questions. Findings 3, 4, 7 are substantive design decisions the authors
should make explicitly before implementation. The rest are tractable
cleanups that the implementing engineer would hit naturally — but
writing them into the spec now saves rework.

**Recommendation:** v2 of all three docs before any code lands.

---

## Blockers (the headline atomicity claim does not hold)

### 1. MULTI/EXEC does NOT abort on a queued-command runtime error

**Affects:** Spec A §Architecture (Phase 4 commit), Spec B §Phase 4 —
extend commit to RENAME staged keys.

Both A and B assume that if `RENAME staged:task:{uid}:{id} → task:{id}`
fails inside `pipe.atomic()`, the whole EXEC is rolled back. **Redis
does not work this way.** EXEC runs every queued command unconditionally;
only queueing-time errors (`EXECABORT`) skip execution. A runtime error
on one command is reported back per-command but does not affect the
others.

Concrete failure mode this opens:

- Phase 1 SETs `staged:task:{uid}:{newId}` with `EX 3600`.
- Phase 2/3/4 take longer than the TTL — pathological but possible
  (slow DEK decrypt of a deeply-nested wire, GC pause, validation that
  pulls many ancestors). Open question 2 in Spec A invites lowering
  the TTL, which makes this less rare.
- Phase 4 EXEC queues `RENAME` (errors: source missing) `+ PERSIST +
  SADD + HSET + RPUSH`. RENAME returns an error; SADD/HSET/RPUSH still
  run.
- Result: `user:{uid}:tasks` now contains `newId`,
  `user:{uid}:item_kinds` says it's a task, but `task:{newId}` does not
  exist. `get_user_tasks` will hit a missing-blob error on every
  subsequent read for that user.
- Spec C's audit cannot recover this: it only SCANs `task:*` keys
  against the user-set, never the reverse direction.

Spec A's open question 3 ("Partial-tree visibility on commit failure")
dismisses this with "per Redis semantics" — the semantics it cites are
the opposite of what Redis actually does.

**Mitigations to prescribe in Spec A's §Architecture:**

- A Lua script (`EVAL`) that does `EXISTS staged → RENAME → SADD/HSET/
  RPUSH` atomically with a precondition check, **or**
- WATCH/MULTI/EXEC with `WATCH staged:task:{uid}:{id}` so a TTL-driven
  loss aborts the EXEC at queueing time, **or**
- Abandon RENAME-from-staging: `SET task:{id} <re-encrypted blob>` +
  `DEL staged:task:{uid}:{id}` inside the pipe, with the same
  precondition check on the staged blob.

### 2. Spec C's final-keyspace audit has a race that destroys live data

**Affects:** Spec C §Final-keyspace audit.

Pre-load `referenced_ids` once at T0; then SCAN+DEL across T0..T2. A
task committed at T1 (Phase 4 of some concurrent batch SETs
`task:{newId}` and SADDs `user:{uid}:tasks`) is **not** in the T0
snapshot. The audit's iteration sees `task:{newId}`, finds it absent
from `referenced_ids`, and DELs it. The user's task is permanently
gone; their `user:{uid}:tasks` set still references a now-empty id.

This violates Spec C's headline contract claim:
*"Idempotent. Safe to call concurrently with `batch_operations` and
with itself."* The §Cross-user safety invariant section asserts
structural impossibility for staged keys (true) but glosses over the
much weaker safety reasoning for the `task:*` audit (false).

Test #6 (`reconciler_does_not_delete_referenced_tasks_under_concurrent_load`)
is named after this case but specified against a quiescent dataset —
under a low spawn count it will pass while the bug is live.

**Mitigations to prescribe:**

- Per-key `SISMEMBER user:{uid}:tasks {id}` re-check **at the moment
  of DEL** — but `task:*` is not uid-namespaced, so this requires
  another lookup path or…
- Minimum age threshold on `task:*` blobs — only DEL if creation age
  exceeds a safe window past any plausible Phase 4 commit, **or**
- Read the blob and skip DEL if `task.date_created` is within a recent
  window.

---

## Major concerns

### 3. `OBJECT IDLETIME` is the wrong primitive for staging age

**Affects:** Spec C §Staging sweep.

`IDLETIME` is "time since last access," not "time since creation."
Several things invalidate it: any `GET`/`SCAN`-with-touch on the key
resets it; `maxmemory-policy` settings (notably the LFU/LRU variants)
change what "access" tracking means; replication and certain backup
mechanisms can touch keys server-side. The spec relies on "no read path
observes staged keys" — but the audit itself is a read path and a
future ops debugging command might be too.

**Prescribe:** embed `created_at` in the staging value, **or** maintain
a `ZADD staged:index <now_ms> "{uid}:{id}"` sorted set in Phase 1 and
`ZRANGEBYSCORE` it in the staging sweep. The latter also makes the
sweep O(orphans) instead of O(staged keyspace).

### 4. Work-item-type scope — only Task; staging contract bakes it in

**The user's explicit question.** Confirmed against
[`item_kind.rs`](../../../../Deferno/backend/src/item_kind.rs): the
four kinds are Task / Habit / Chore / Event. The spec set covers
**Task only**:

- `BatchOperation` lives in [`payloads.rs:265-278`](../../../../Deferno/backend/src/payloads.rs)
  which is task-only by file location and import surface.
- Spec B's `queue_visibility_flip` hard-codes
  `HSET user:{uid}:item_kinds {id} "task"`.
- Staging key shape is `staged:task:{uid}:{newId}` — the kind is in
  the *prefix*.

If the goal is "batch as the canonical multi-create primitive" (the
MCP `batch` tool docstring and Spec B §Why imply this), three options,
all worth deciding now:

1. **Document explicitly that batch is task-only**, with parallel
   `/habits/batch`, `/chores/batch`, `/events/batch` to follow as
   separate specs sharing the four-phase model. Cheap, but the MCP
   `batch` tool name oversells it.
2. **Generalize the staging contract today** to
   `staged:{kind}:{uid}:{id}` and parameterize the kind through Phase
   1/4 helpers. The reconciler's SCAN pattern becomes `staged:*:*` or
   `staged:{kind}:*`. **No migration needed** because the namespace is
   being introduced fresh by Spec A — there is no installed base of
   `staged:task:*` keys yet. Doing this later is a key-shape
   migration; doing it now is free.
3. **Mixed-kind in one batch** is a separate, harder design (recurrence
   semantics differ per kind). Probably should remain a non-goal even
   under option 2.

**Recommendation:** option 2 — same change in Spec A, near-zero cost,
prevents a contract migration later.

### 5. Validation order leaves Phase 1 doing wasted work that subsequently has to be cleaned up

**Affects:** Spec B §Phase 1.

The handler already pre-loop-validates Update/Move target ownership.
Spec B §Handler-side adds the same for top-level Create's `parent_id`,
plus a recursive `walk_reject_nested_parent_id`. Recurrence rejection
runs at deserialize time. Update target existence is checked by the
existing pre-loop — so a batch with a bad Update target 404s **before**
Phase 1 stages anything.

The actual gap: a batch with valid pre-loop checks but a **status
cascade rejection in Phase 3** ("cannot complete task while children
remain active") will have already SET N staging keys. Phase 5 must DEL
them all sequentially.

Worth calling out in Spec A's open questions — could either (a) accept
the cost and rely on synchronous DEL + reconciler, (b) move the cascade
check up before Phase 1 by pre-fetching ancestor chains, or (c) do
Phase 1 inside an MULTI/EXEC pipe so the wasted work is at least
bounded to one round-trip per batch.

### 6. Phase 1 is N synchronous round-trips, no atomicity

**Affects:** Spec B §Phase 1 (`stage_one_create_node`).

Each node SET is a separate `query_async`. For a 50-node tree that's
50 round-trips before the validation gate even fires.

**Easy fix:** pipeline (non-transactional `redis::pipe()` without
`.atomic()`) all SETs in one round-trip. The reconciler is already the
cleanup guarantee, so non-atomic pipelining of the stage phase is safe.

Also worth specifying explicitly: what happens if the connection drops
mid-pipeline. Today the spec is silent; the reconciler catches it but
the spec should say so.

### 7. No DoS bounds anywhere

Neither spec bounds:

- Total `operations` array length — a malicious or buggy MCP caller
  can ship 100k ops.
- Tree depth — `BatchCreateFields::children` recurses without limit.
  Boxed-future recursion + recursive parsing/encryption hits server
  limits before pretty failure.
- Children per node.
- Total nodes per batch.

**Prescribe explicit bounds** and reject at deserialize/handler entry:
`operations.len() <= 200`, depth <= 32, total_nodes <= 1024 (numbers to
taste). Without this, the API is exposed to amplification and the
reconciler has to clean up after every misuse.

### 8. Lost-update on parent's `children` array is worse-shaped than today

**Affects:** Spec A §Concurrency note (acknowledged but underweighted).

Spec A acknowledges this as a non-goal ("ETag-based lost-update
detection is a separate spec") but the failure mode under the new
design is *worse-shaped* than current single-task `update_task`:

- Today: two concurrent updates to task X serialize on `save_task`.
  Last write wins.
- After Spec B: batch A is mid-flight Phase 2 (has X in memory),
  batch B commits a Create whose `parent_id == X` so X.children gains
  a new id. Batch A's Phase 4 SET overwrites X with batch A's pre-B
  view of X, dropping the just-added child reference. The new child
  still exists in `task:{newChild}` and `user:{uid}:tasks` — but X no
  longer references it. **Inconsistent state, not just stale.**
  Tree-walk readers will surface this.

Spec A's claim that "this matches today's behaviour and is not made
worse by this spec" is true *for the pure update-vs-update case* but
the addition of cross-batch parent-mutation through Create makes it
worse. Either make it a hard non-goal in Spec B explicitly, or add
WATCH on parent keys for the affected pre-existing tasks during
Phase 2.

### 9. Spec C contract claim vs. delivery

**Affects:** Spec C §Public API and §Cross-user safety invariant.

Spec C documents `reconcile_orphan_tasks` as **"Idempotent. Safe to
call concurrently with `batch_operations` and with itself."** Finding 2
breaks this guarantee. The §Cross-user safety invariant section
asserts structural impossibility for staged keys (true) but its
reasoning for the final-keyspace audit boils down to "the pre-loaded
referenced-id set protects you" — which is exactly the snapshot whose
race condition causes data loss. The §Cross-user safety invariant text
needs to be honest about the audit's weaker guarantees, not just
declare safety.

---

## Minor / observations

- **`item_kinds` HSET hardcodes `"task"`** in Spec B's
  `queue_visibility_flip`. Same root cause as #4.
- **`PERSIST` after `RENAME`** is correct (RENAME inherits TTL) but two
  queued commands per visibility flip when one would do via
  `SET task:{id} <blob> + DEL staged:...`. If you switch to the
  SET+DEL approach for the EXEC fix in #1, this falls out
  automatically.
- **`STAGE_TTL_SECS` env-var read inside a per-node helper** —
  re-parsed on each recursive call. Trivial; move to a config
  singleton or a parameter.
- **Reconciler periodic loop has no jitter** — N replicas booting
  together hammer Redis in lockstep. Add a per-process random offset.
- **Reconciler swallowed errors on synchronous DEL** —
  `let _ = ...` should at least `tracing::debug!` so ops can correlate
  orphan accumulation with DEL failure spikes. Spec says "fall through
  to tier 2" — fine, but tier 2 needs a signal.
- **Test #6** (`reconciler_does_not_delete_referenced_tasks_under_concurrent_load`)
  is the test that should detect finding 2 — it doesn't, because the
  spec hasn't told it what to look for. Re-spec to: spawn one task
  that creates new tasks back-to-back via the normal handler, in
  parallel with `reconcile_orphan_tasks` running on a tight loop, and
  assert `get_all_tasks` count is monotonically non-decreasing
  throughout.
- **Test 7a** (`batch_failure_with_redis_blip_leaves_staging_for_reconciler`)
  requires injectable Redis fault — that test infra doesn't exist yet
  and isn't specced. Either spec the test adapter or note it as a
  prerequisite.
- **Implicit-parent rule** — the deserializer permits `parent_id` on
  nested creates and the handler rejects it. Cleaner: a separate
  `BatchCreateChildFields` struct without `parent_id`. But this fights
  `serde(flatten)` and the recursion shape; current approach is
  acceptable.

---

## Source-of-truth references consulted during review

- [`Deferno/backend/src/payloads.rs:84-278`](../../../../Deferno/backend/src/payloads.rs)
  (`reject_recurrence_hint_fields`, `CreateTaskPayload`,
  `BatchOperation`).
- [`Deferno/backend/src/handlers/tasks.rs:430-544`](../../../../Deferno/backend/src/handlers/tasks.rs)
  (existing `batch_tasks` handler — the pre-loop validation
  referenced in finding 5).
- [`Deferno/backend/src/repository/batch.rs:1-114`](../../../../Deferno/backend/src/repository/batch.rs)
  (the current sequential flush whose atomicity gap motivates
  Spec A).
- [`Deferno/backend/src/item_kind.rs`](../../../../Deferno/backend/src/item_kind.rs)
  (the four `ItemKind` variants — Task / Habit / Chore / Event —
  underpinning finding 4).
- [`Deferno/backend/src/work_item.rs`](../../../../Deferno/backend/src/work_item.rs)
  (the polymorphic `WorkItem` trait that habits/chores/events also
  implement).
