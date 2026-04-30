<!-- ───────────────────────────── WIP BANNER ───────────────────────────── -->
> # ⚠️⚠️⚠️ WIP — DO NOT IMPLEMENT ⚠️⚠️⚠️
>
> **Status:** work-in-progress. Has unresolved design issues, including
> at least two blocker-level findings (the headline atomicity guarantee
> does not hold as written).
>
> **Do NOT propose, plan, or implement work based on this document
> unless Kyle explicitly asks to revisit this feature.**
>
> Do not surface this spec speculatively, do not cite it as a source of
> truth for batch behavior, and do not pull tasks from it during
> autonomous work.
>
> Open issues are catalogued in
> [`2026-04-29-batch-trio-design-feedback.md`](2026-04-29-batch-trio-design-feedback.md).
> Resolve those (and re-spec accordingly) before this document becomes
> actionable.
<!-- ────────────────────────── END WIP BANNER ──────────────────────────── -->

---

# `/tasks/batch` — atomicity refactor — design

**Status:** draft
**Date:** 2026-04-29
**Repos:** `Deferno` (Rust backend)
**Source of truth:** the Rust types in [`Deferno/backend/src/payloads.rs`](../../../../Deferno/backend/src/payloads.rs) and the handler in [`Deferno/backend/src/handlers/tasks.rs`](../../../../Deferno/backend/src/handlers/tasks.rs).

**Related specs (sibling — same date):**
- [`2026-04-29-orphan-reconciler-design.md`](2026-04-29-orphan-reconciler-design.md) — defense-in-depth audit that catches any unaccounted task blob (from this refactor or any other source). Independent.
- [`2026-04-29-batch-create-variant-design.md`](2026-04-29-batch-create-variant-design.md) — adds `BatchOperation::Create` on top of the model defined here. **Depends on this spec.**

---

## Why

The current `batch_operations`
([`repository/batch.rs:18-114`](../../../../Deferno/backend/src/repository/batch.rs))
documents itself as atomic:

> Execute a sequence of update/move operations atomically.
>
> All mutations are applied against in-memory copies of tasks. If
> every operation succeeds the dirty tasks are flushed to Redis in
> bulk; if any operation fails nothing is persisted.

But the actual flush at lines 73-105 is a sequential
`?`-shortcircuit loop: per-task `save_task`, then per-task
`index_task`, then per-root-op `root_order_*`. Each is its own
Redis call. A network blip on the 4th `save_task` leaves tasks
1-3 persisted, the rest unpersisted, and the search index in an
inconsistent state. The doc comment overstates the guarantee.

This spec replaces that flush with a model that does deliver true
atomicity. It also defines the **staging-key mechanism** that a
sibling spec ([`2026-04-29-batch-create-variant-design.md`](2026-04-29-batch-create-variant-design.md))
will use for atomic creates — but no `Create` variant is added
here. The scope is the existing `Update` and `Move` ops only.

## Goal

Refactor `batch_operations` so the entire operation list (today
just `Update` and `Move`; later `Create` once the sibling spec
lands) commits or rolls back as a single atomic unit, and define
the four-phase **stage / wire / commit / cleanup** model that
both this refactor and the future `Create` variant share.

### Success criteria

1. The four-phase model (stage / wire / commit / cleanup) is
   defined and documented in `repository/batch.rs`.
2. `batch_operations` for existing `Update` and `Move` ops uses
   a single `redis::pipe().atomic()` (`MULTI`/`EXEC`) block for
   the commit phase. Either every queued command runs or none
   do.
3. Phase 1 (stage) and Phase 5 (cleanup) are no-ops in this
   spec — no operation today produces staged content. The model
   leaves clean extension points so the `Create` variant can
   plug in stage and cleanup logic without restructuring.
4. The staging-key shape is fixed by this spec as a contract:
   `staged:task:{uid}:{newId}`. uid in the key prevents
   cross-user contamination; the `staged:` prefix marks the key
   as not-yet-committed. No code in this spec writes such keys,
   but the contract is specified so the sibling Create spec and
   the reconciler spec can plan around it.
5. RED tests cover the atomic-rollback path on existing op
   types (a failure mid-batch leaves no mutation persisted).
6. The doc comment on `batch_operations` is rewritten to
   accurately describe the guarantee.

## Non-goals

- **Adding a `Create` variant.** That is the sibling spec
  [`2026-04-29-batch-create-variant-design.md`](2026-04-29-batch-create-variant-design.md).
- **The reconciler.** Synchronous cleanup is sufficient for v1
  of this spec because no staging happens here. The reconciler
  is the sibling spec [`2026-04-29-orphan-reconciler-design.md`](2026-04-29-orphan-reconciler-design.md).
- **Cluster-mode multi-key transactions.** v1 assumes a single
  Redis instance. Cluster compatibility is open question 1.
- **Lost-update detection.** Two concurrent batches updating the
  same task: last EXEC wins. This matches today's behaviour and
  is not made worse by this spec. ETag-based lost-update
  detection is a separate spec.

---

## Architecture

### The four-phase model

The atomicity guarantee separates *content write* (heavy, may
fail, may need cleanup) from *visibility flip* (small, single
atomic instruction, what the user observes).

**Phase 1 — stage.** For every new task in the batch (every
future `Create` op once that variant lands), mint a UUID and
write the encrypted blob to a **staging key** `staged:task:{uid}:{newId}`.
Staging keys are:

- **uid-namespaced** — the user id is part of the key. Cleanup
  operating on user A's failed batch cannot affect user B's
  data because the keys are distinct.
- **prefixed with `staged:`** — distinguishes uncommitted
  content from committed content (`task:{id}`). The
  reconciler's audit sweeps `staged:task:*` to find orphans
  whose explicit cleanup failed.
- **TTL'd** — defense-in-depth. Staged keys carry an explicit
  TTL (`STAGE_TTL_SECS`, default 3600). If both the
  synchronous cleanup AND the reconciler fail to fire, Redis
  itself eventually drops the key. TTL is **not** the
  guarantee — it is belt-and-suspenders. The guarantee is
  reconciler + synchronous DEL.

In this spec (atomicity refactor) Phase 1 is a no-op: no `Create`
op exists, so nothing stages. The phase is defined as a method
that returns an empty staged-tree. The sibling Create spec fills
it in.

After Phase 1, any future staged tasks exist in storage as
unreachable orphans:

- `staged:task:{uid}:{newId}` is set in Redis.
- `user:{uid}:tasks` does NOT yet contain `newId`. So
  `get_user_tasks` cannot see it.
- `user:{uid}:item_kinds` does NOT yet contain `newId`. So the
  cross-kind lookups cannot see it.
- `user:{uid}:root_order` does NOT yet contain `newId`. So the
  task list view cannot see it.
- The search index does NOT yet contain `newId`. So search
  cannot find it.
- The final key `task:{newId}` does NOT exist. RENAME at commit
  time is what moves the blob into the final keyspace.

The user observes nothing has changed.

**Phase 2 — wire (in memory only).** For every `Update` and
`Move` op, apply the mutation against an in-memory copy of the
existing task. **Nothing is written to storage in this phase.**
All mutations are buffered in `Vec<Task>` / `Vec<RootOrderOp>` /
similar data structures held in the handler scope.

**Phase 3 — validate.** The buffered mutations are inspected for
contract violations:

- task-not-found, parent-not-owned, status cascade rejections,
  cycle checks for `Move`, recurrence-key presence, etc.

If validation fails, jump to Phase 5 (cleanup) and return the
appropriate `ApiError`. Because Phase 1 was a no-op for this
spec, cleanup has nothing to do at this stage of v1.

**Phase 4 — commit.** A single `redis::pipe().atomic()`
(`MULTI`/`EXEC`) block performs every storage mutation:

1. For each staged task (none in this spec; all in the Create
   spec): RENAME `staged:task:{uid}:{newId}` → `task:{newId}`
   ; PERSIST `task:{newId}` (RENAME transfers TTL — PERSIST
   removes it from the final key); SADD `user:{uid}:tasks` ;
   HSET `user:{uid}:item_kinds` ; LPUSH `user:{uid}:root_order`
   if root.
2. For each existing task with buffered mutations (Updates,
   Moves, parent-gained-child): SET its `task:{id}` blob to
   the new in-memory state.
3. For each Move that changes root membership: ZADD/LREM
   `user:{uid}:root_order`.
4. For each new and each mutated task: HSET its search-index
   row.

EXEC either runs every queued command or none. The pattern
already used by [`comments.rs:62-63`](../../../../Deferno/backend/src/repository/comments.rs),
[`tasks.rs:99-104`](../../../../Deferno/backend/src/repository/tasks.rs),
and across `chores.rs` / `events.rs` / `habits.rs`.

**Phase 5 — cleanup-on-failure.** If validation (Phase 3) or
commit (Phase 4) fails, every staged orphan must be removed.

Cleanup is a two-tier guarantee:

1. **Synchronous DEL.** Inside the handler's error path,
   attempt `DEL staged:task:{uid}:{newId}` for every staged
   orphan. Single round-trip, no retry — if it fails, fall
   through to tier 2.
2. **Reconciler audit.** The sibling spec
   [`2026-04-29-orphan-reconciler-design.md`](2026-04-29-orphan-reconciler-design.md)
   periodically SCANs `staged:task:*` and DELs entries that
   have been there longer than `STAGE_TTL_SECS / 2` (so a
   sweep happens before TTL fires; TTL is the last-resort
   safety net).

Because no staging happens in this spec, Phase 5 is also a
no-op for v1 of the refactor. The phase is wired in and tested
once the Create spec lands; this spec defines the contract.

### Pseudocode for the new `batch_operations`

```rust
pub async fn batch_operations(
    &self,
    ops: Vec<BatchOp>,
    user_id: Uuid,
    dek: &SecretKey,
) -> Result<BatchResult> {
    let mut conn = self.get_connection().await?;

    // Tracks every newId we wrote in Phase 1 so cleanup-on-failure
    // can find them. Holding this outside the inner async block
    // means the catch arm has access regardless of which phase
    // failed. Empty in this spec; populated by the Create spec.
    let mut staged_orphans: Vec<(Uuid, Uuid)> = Vec::new(); // (uid, newId)

    let result: Result<BatchResult> = async {
        // ── Phase 1: stage new tasks ────────────────────────
        // No-op in this spec — no Create variant exists yet.
        // The Create spec replaces this with stage_create_tree.
        let staged = self.stage_nothing();

        // ── Phase 2: wire existing-task mutations in memory ──
        // Apply every Update and Move op to an in-memory cache
        // of the affected pre-existing tasks. NOTHING writes here.
        let mutations = self
            .wire_existing_mutations(&mut conn, &ops, &staged, user_id, dek)
            .await?;

        // ── Phase 3: validate ───────────────────────────────
        self.validate_batch(&staged, &mutations, user_id)?;

        // ── Phase 4: commit (single atomic block) ───────────
        let mut pipe = redis::pipe();
        pipe.atomic();
        for new_task in &staged.tasks {
            queue_visibility_flip(&mut pipe, user_id, new_task);
        }
        for mutated in &mutations.tasks {
            queue_blob_write(&mut pipe, mutated, dek)?;
        }
        for root_op in &mutations.root_order_ops {
            queue_root_order_op(&mut pipe, user_id, root_op);
        }
        pipe.query_async::<()>(&mut conn).await?;

        Ok(BatchResult {
            tasks: collect_response_tasks(&staged, &mutations),
        })
    }
    .await;

    // ── Phase 5: cleanup-on-failure ─────────────────────────
    // Synchronous DEL is the fast path. The reconciler is the
    // guarantee — see 2026-04-29-orphan-reconciler-design.md.
    if result.is_err() && !staged_orphans.is_empty() {
        for (uid, orphan_id) in &staged_orphans {
            let _ = redis::cmd("DEL")
                .arg(Self::staged_task_key(*uid, *orphan_id))
                .query_async::<()>(&mut conn)
                .await;
        }
    }

    result
}
```

The functions `wire_existing_mutations`, `validate_batch`,
`queue_visibility_flip`, `queue_blob_write`, `queue_root_order_op`,
and `collect_response_tasks` are private helpers on
`TaskRepository`. They directly correspond to the phase
descriptions above. Mirror the existing helper shapes in
[`repository/batch.rs`](../../../../Deferno/backend/src/repository/batch.rs)
(`apply_update`, `apply_move`) for code style.

### Key-name helpers to add

```rust
// Deferno/backend/src/repository/tasks.rs (additions)

impl TaskRepository {
    /// Final, committed task content key. Existing.
    fn task_key(id: Uuid) -> String {
        format!("task:{id}")
    }

    /// Staging key for a task that has been written but not yet
    /// committed. uid is in the key for cross-user safety; the
    /// `staged:` prefix lets the reconciler's SCAN find it.
    fn staged_task_key(uid: Uuid, id: Uuid) -> String {
        format!("staged:task:{uid}:{id}")
    }
}
```

### Concurrency note

Two concurrent batches by the same user can race in Phase 1
(both stage their orphans into distinct staging keys), then
serialise on Phase 4 (each EXEC commits independently). For
batches that *update the same existing task*, the last EXEC
wins — there is no row-level locking. This matches the existing
single-task `update_task` semantics
([`repository/tasks.rs::update_task`](../../../../Deferno/backend/src/repository/tasks.rs))
and is not made worse by this spec.

---

## Test plan

### Backend integration tests

**File:** `Deferno/backend/src/handlers/tasks.rs` (existing). Add
a new `#[cfg(test)] mod batch_atomicity_tests` block after the
existing `import_version_tests` block at line 814.

**Style precedent:** `delete_task_tests` at [`tasks.rs:760-812`](../../../../Deferno/backend/src/handlers/tasks.rs)
(uses `seed_state_and_user`, `#[serial]` ordering, `axum::Json`).

**Tests to add:**

1. **`batch_update_move_atomic_on_success`**
   Pre-create tasks X and Y. Run a 2-op batch: update X's
   status, move Y under X. Assert: response contains both;
   re-fetched X has new status; re-fetched X.children contains
   Y.id; re-fetched Y.parent_id == X.id.

2. **`batch_update_failure_rolls_back_all_mutations`**
   Pre-create tasks X and Y. Run a 3-op batch:
   - op #0: update X to status `done`
   - op #1: update Y to status `done`
   - op #2: update a non-existent task id (forces 404)
   Assert: the call returns 404. Re-fetch X — its status is
   UNCHANGED (NOT `done`). Re-fetch Y — its status is also
   UNCHANGED. The atomic block never EXEC'd because validation
   failed first; no SET on existing task blobs ran.

3. **`batch_move_cycle_rejection_rolls_back`**
   Pre-create tasks X and Y where Y is X's child. Run a 2-op
   batch:
   - op #0: update X's status
   - op #1: move X under Y (creates a cycle)
   Assert: the call returns 4xx for the cycle. Re-fetch X — its
   status is UNCHANGED.

4. **`batch_root_order_atomic`**
   Pre-create root tasks X (position 0), Y (position 1), Z
   (position 2). Run a batch of moves that reorders them. Assert
   the resulting order is exactly the requested permutation.
   Verify that on a forced mid-batch failure, the root order is
   identical to before the batch began.

These tests exercise Phase 2 + Phase 3 + Phase 4 (the only
phases this spec activates). Phase 1 and Phase 5 tests live in
the Create variant spec.

---

## Backwards compatibility

### The atomicity refactor changes observed behaviour

This is a non-purely-additive change. Today's `batch_operations`
can leave a partial-flush mid-failure. After this spec, it
cannot — the new model holds every mutation in memory until
the commit block, and the commit block is one EXEC.

This is a **strict improvement**: any caller that relied on the
documented contract ("if any operation fails nothing is
persisted") gets exactly that. Any caller that observed the
undocumented partial-flush behaviour was relying on a bug.
There is no breakage.

### No migration

No schema migration. No Redis-key shape change for existing
keys. The `staged:task:*` keyspace is new but unused in this
spec (it is reserved for the Create spec). No API-version bump.

---

## Open questions

1. **Cluster-mode slot routing.** RENAME requires source and
   destination keys to live on the same Redis cluster slot. v1
   targets a single Redis instance, where this is automatic. If
   Deferno later moves to Redis cluster, the staging-key and
   final-key names must share a hash tag — e.g.,
   `staged:task:{{uid}}:{newId}` and `task:{{uid}}:{newId}`,
   where `{{uid}}` is a Redis hash tag forcing same-slot
   placement. Out of scope for v1; revisit when cluster mode is
   considered.

2. **STAGE_TTL_SECS default.** 3600s (1 hour) is a guess at
   "longer than any plausible batch handler runtime, shorter
   than ops would tolerate orphan keys persisting." Empirical
   observation after the Create spec lands should either
   confirm or recalibrate this. Configure via env var so
   adjustment is a deploy, not a code change.

3. **Search index inside MULTI/EXEC.** RediSearch HSET'ing the
   index hash inside a `pipe().atomic()` works because the
   index module hooks Redis hash-mutation events. The search
   index update is therefore part of the same atomic commit as
   the content write. If a future search backend (e.g.,
   external Elasticsearch) is adopted, the search-index update
   becomes a non-atomic side-effect that needs its own commit-
   or-rollback story. Out of scope for v1.
