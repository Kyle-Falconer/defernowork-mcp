<!-- ───────────────────────────── WIP BANNER ───────────────────────────── -->
> # ⚠️⚠️⚠️ WIP — DO NOT IMPLEMENT ⚠️⚠️⚠️
>
> **Status:** work-in-progress. The headline contract claim
> ("idempotent, safe to call concurrently with `batch_operations` and
> with itself") is NOT delivered by the design as written — the
> final-keyspace audit has a snapshot race that can destroy live data.
>
> **Do NOT propose, plan, or implement work based on this document
> unless Kyle explicitly asks to revisit this feature.**
>
> Do not surface this spec speculatively, do not cite it as a source of
> truth for reconciler behavior, and do not pull tasks from it during
> autonomous work.
>
> Open issues are catalogued in
> [`2026-04-29-batch-trio-design-feedback.md`](2026-04-29-batch-trio-design-feedback.md).
> Resolve those (and re-spec accordingly) before this document becomes
> actionable.
<!-- ────────────────────────── END WIP BANNER ──────────────────────────── -->

---

# Orphan reconciler — design

**Status:** draft
**Date:** 2026-04-29
**Repos:** `Deferno` (Rust backend)
**Source of truth:** the repository helpers in [`Deferno/backend/src/repository/tasks.rs`](../../../../Deferno/backend/src/repository/tasks.rs) and the per-user task set keys (`user:{uid}:tasks`, `user:{uid}:item_kinds`, `user:{uid}:root_order`).

**Related specs (sibling — same date):**
- [`2026-04-29-batch-atomicity-refactor-design.md`](2026-04-29-batch-atomicity-refactor-design.md) — defines the `staged:task:{uid}:{newId}` keyspace this reconciler audits. Independent.
- [`2026-04-29-batch-create-variant-design.md`](2026-04-29-batch-create-variant-design.md) — produces the staging keys this reconciler may need to clean up after a failed Create.

---

## Why

Task content can become unreferenced:

1. **Failed batch creates** (after [`2026-04-29-batch-create-variant-design.md`](2026-04-29-batch-create-variant-design.md)
   lands): a batch stages content to `staged:task:{uid}:{newId}`, then
   fails before the atomic commit. Synchronous DEL is the fast path
   for cleaning these up; if the DEL itself fails (network blip,
   process crash, Redis unavailable), the staging key persists.
2. **Pre-batch orphans** (already possible today): the existing
   single-task code paths can leave `task:{id}` blobs without
   matching entries in `user:{uid}:tasks` if a process crashes
   between the SET and the SADD. This is rare under normal
   operation but real, and there is no current cleanup.
3. **Manual / migration writes**: any future operator script,
   ETL, or data migration that writes `task:*` keys without
   updating the membership sets produces orphans.

In every case the data is unreachable through normal read paths
(it has no membership in `user:{uid}:tasks`) but it consumes
keyspace. Worse, an orphan `task:{id}` can persist indefinitely
with no observability — the team has no way to know whether the
keyspace contains 0 orphans or 100k.

This spec adds a **reconciler** that periodically audits two
keyspaces, deletes anything that is genuinely unreachable, and
reports the result. The reconciler is the **guarantee** behind
the staging-key cleanup story; it is not best-effort. TTL on
staging keys (defined by the atomicity refactor spec) is
defense-in-depth — the reconciler exists so that no operational
correctness depends on Redis honoring its TTL.

## Goal

Add a `reconcile_orphan_tasks` method on `TaskRepository`, wired
into the server lifecycle (startup, periodic, shutdown), that
identifies and deletes orphan task blobs from two sources:

- `staged:task:{uid}:{newId}` keys that have been there longer
  than a safe threshold (failed batch stagings whose explicit
  cleanup did not complete).
- `task:{id}` keys whose id is not in any `user:{uid}:tasks` set
  AND not referenced by any persisted task's `children` array
  (orphans from any source).

### Success criteria

1. `TaskRepository::reconcile_orphan_tasks() -> Result<ReconcileReport>`
   exists, is `pub`, and is idempotent (safe to call concurrently
   with batch operations and with itself).
2. The reconciler uses Redis `SCAN` (not `KEYS` — `KEYS` blocks
   the server). Sweeps are progress-resumable: if interrupted
   between cursors, the next call picks up wherever the keyspace
   is now.
3. Two sweeps:
   - **Staging sweep:** `SCAN MATCH staged:task:*`. For each
     match, parse the uid from the key. If the key's age (via
     Redis `OBJECT IDLETIME`) exceeds `STAGE_RECONCILE_AGE_SECS`
     (default 1800 — half of the staging TTL), DEL the key.
   - **Final-keyspace audit:** `SCAN MATCH task:*`. For each
     match, look up the id across all `user:*:tasks` sets and
     all persisted task `children` arrays. If unreferenced, DEL.
4. Lifecycle wiring:
   - **Startup hook** runs one full reconcile pass before the
     Axum router binds to a port. Failures here are logged but
     non-fatal.
   - **Periodic task** spawned at startup runs every
     `RECONCILER_INTERVAL_SECS` (default 300). Errors are
     logged at WARN; the loop continues.
   - **Shutdown signal** (SIGTERM / Ctrl-C) triggers one final
     pass before the server exits.
5. Cross-user safety invariant: the reconciler never reads from,
   writes to, or affects user B's data while processing user A's
   orphans. Per-key DEL is uid-isolated by construction; the
   final-keyspace audit's join logic walks per-user sets in a
   single pass and emits per-key DELs only.
6. `ReconcileReport` exposes counters and IDs that surface in
   logs for observability — ops can grep server logs to know how
   many orphans drained on the last sweep.
7. RED tests cover both sweeps and the lifecycle wiring.

## Non-goals

- **Modifying the existing batch / single-task code paths.** The
  reconciler is a new module; existing handlers are untouched.
- **Real-time orphan detection.** The reconciler runs on a
  schedule. Orphans persist for at most `RECONCILER_INTERVAL_SECS`
  before being detected (300s default).
- **Cluster-mode SCAN coordination.** v1 targets a single Redis
  instance. Cluster mode would require per-shard SCAN; out of
  scope.
- **Cleaning up orphan content from non-task entities** (chores,
  events, habits, items). Their keyspace shapes are similar but
  each has its own membership semantics. Out of scope; future
  work if a similar issue surfaces there.

---

## Architecture

### Module layout

```
Deferno/backend/src/repository/
  orphan_reconciler.rs   # NEW — the reconciler module
  mod.rs                 # add `pub mod orphan_reconciler;`

Deferno/backend/src/
  main.rs                # add startup hook + periodic spawn + shutdown hook
```

### Public API

```rust
// Deferno/backend/src/repository/orphan_reconciler.rs

pub struct ReconcileReport {
    /// Staging keys older than the threshold that we DEL'd.
    pub staged_swept: usize,
    /// Final-keyspace `task:{id}` blobs that had no membership
    /// in any user:*:tasks set and no children-array reference,
    /// and that we DEL'd.
    pub final_audit_swept: usize,
    /// IDs whose DEL failed during this sweep. Logged for
    /// observability; will be retried on the next pass.
    pub failed_dels: Vec<String>,
}

impl TaskRepository {
    /// Audit the keyspace for orphan task content and DEL.
    /// Idempotent. Safe to call concurrently with
    /// batch_operations and with itself.
    pub async fn reconcile_orphan_tasks(&self) -> Result<ReconcileReport>;
}
```

### Staging sweep

```rust
async fn sweep_staging(&self, conn: &mut redis::aio::Connection)
    -> Result<(usize, Vec<String>)>
{
    let mut cursor: u64 = 0;
    let mut swept = 0;
    let mut failed: Vec<String> = Vec::new();
    let age_threshold: u64 = std::env::var("STAGE_RECONCILE_AGE_SECS")
        .ok().and_then(|s| s.parse().ok()).unwrap_or(1800);

    loop {
        let (next, keys): (u64, Vec<String>) = redis::cmd("SCAN")
            .arg(cursor)
            .arg("MATCH").arg("staged:task:*")
            .arg("COUNT").arg(500)
            .query_async(conn).await?;

        for key in keys {
            // OBJECT IDLETIME returns seconds since last access.
            // For staging keys, "last access" is roughly when SET
            // wrote them — they're not read except by RENAME at
            // commit time, which happens within seconds of the SET
            // for any successful batch.
            let idle: Option<u64> = redis::cmd("OBJECT")
                .arg("IDLETIME").arg(&key)
                .query_async(conn).await.ok();

            if let Some(seconds) = idle {
                if seconds >= age_threshold {
                    let del_res: redis::RedisResult<u64> =
                        redis::cmd("DEL").arg(&key)
                            .query_async(conn).await;
                    match del_res {
                        Ok(_) => swept += 1,
                        Err(_) => failed.push(key),
                    }
                }
            }
        }

        cursor = next;
        if cursor == 0 { break; }
    }

    Ok((swept, failed))
}
```

The `OBJECT IDLETIME` semantics are key. Redis tracks per-key
idle time. A read or write resets it. For a staging key:

- Phase 1 SET writes it → idle = 0.
- Phase 4 RENAME (on success) destroys the staging key entirely
  → it's gone from this scan's perspective.
- If the batch fails and synchronous DEL in Phase 5 succeeds →
  also gone.
- If both Phase 4 and Phase 5 didn't run (process crash, etc.)
  → idle keeps growing until the reconciler finds it.

`STAGE_RECONCILE_AGE_SECS` (default 1800 = 30 min) must be:

- Long enough to never racing a legitimately in-flight batch.
  No batch handler should plausibly take 30 minutes; 1 minute
  would also work but 30 gives generous headroom.
- Shorter than `STAGE_TTL_SECS` (default 3600 = 1 hour) so the
  reconciler beats the TTL — TTL is the last-resort backstop,
  not the primary mechanism.

### Final-keyspace audit

```rust
async fn audit_final_keyspace(&self, conn: &mut redis::aio::Connection)
    -> Result<(usize, Vec<String>)>
{
    let mut cursor: u64 = 0;
    let mut swept = 0;
    let mut failed: Vec<String> = Vec::new();

    // Pre-load every user:*:tasks set into a single in-memory
    // HashSet<Uuid> of "all referenced ids." Pre-loading once
    // is cheaper than re-querying per scanned key.
    let referenced_ids = self.collect_all_referenced_ids(conn).await?;

    loop {
        let (next, keys): (u64, Vec<String>) = redis::cmd("SCAN")
            .arg(cursor)
            .arg("MATCH").arg("task:*")
            .arg("COUNT").arg(500)
            .query_async(conn).await?;

        for key in keys {
            // Filter: skip the staged: prefix (different namespace).
            if key.starts_with("staged:") { continue; }

            // Parse the uuid from "task:{id}".
            let id_str = match key.strip_prefix("task:") {
                Some(s) => s,
                None => continue,
            };
            let id = match Uuid::parse_str(id_str) {
                Ok(u) => u,
                Err(_) => continue, // unrecognized key shape
            };

            if !referenced_ids.contains(&id) {
                let del_res: redis::RedisResult<u64> =
                    redis::cmd("DEL").arg(&key)
                        .query_async(conn).await;
                match del_res {
                    Ok(_) => swept += 1,
                    Err(_) => failed.push(key),
                }
            }
        }

        cursor = next;
        if cursor == 0 { break; }
    }

    Ok((swept, failed))
}

async fn collect_all_referenced_ids(
    &self,
    conn: &mut redis::aio::Connection,
) -> Result<HashSet<Uuid>> {
    // SCAN user:*:tasks ; SUNION every set into a HashSet.
    // Then re-walk every persisted task's children array and
    // union those ids in too — children arrays reference task
    // ids that are sometimes only reachable transitively.
    //
    // The full implementation is straightforward but not shown
    // here in the interest of spec brevity. Mirror the SUNION
    // helper pattern in repository/tasks.rs::get_user_tasks.
    todo!()
}
```

The pre-load is the only memory-heavy step: it materializes
every task id referenced anywhere into one HashSet. For a deploy
with millions of tasks, this is a ~16-byte-per-entry HashSet
(UUID + overhead) — 16 MB per million tasks. Acceptable for v1.

If the deploy ever scales beyond a single-process audit, the
audit can be replaced with a streaming join (Bloom filter +
second pass), but that is premature optimization at this stage.

### Cross-user safety invariant

The reconciler MUST NOT, under any circumstances, DEL data that
belongs to user A while operating on user B's behalf. The design
honors this:

1. **Staging sweep** — keys carry the uid in their name
   (`staged:task:{uid}:{id}`). Per-key DEL affects exactly that
   one key. The reconciler does not need to know which user a
   key belongs to in order to safely DEL it; it just DELs the
   matched key. Cross-user contamination is structurally
   impossible.

2. **Final-keyspace audit** — `task:{id}` keys are not
   uid-namespaced (they predate this spec). The audit's safety
   relies on the pre-loaded referenced-id set: an id present in
   ANY user's `user:*:tasks` set or ANY task's children array is
   skipped. This guarantees that user A's task is never DEL'd
   while user B has a reference to it (which would not happen
   in practice — task ids are user-scoped — but the audit is
   safe even under adversarial conditions).

3. **No writes to per-user state.** The reconciler emits only
   DEL commands. It does not SADD, SREM, HSET, or otherwise
   modify membership sets. The user's view of their tasks
   cannot be altered by the reconciler.

This invariant is checked by test
`reconciler_does_not_delete_referenced_tasks_under_concurrent_load`
below.

### Lifecycle wiring

```rust
// Deferno/backend/src/main.rs (additions)

use repository::orphan_reconciler::*;

#[tokio::main]
async fn main() -> Result<()> {
    let app_state = build_app_state().await?;

    // ── Startup hook ──
    match app_state.repository.reconcile_orphan_tasks().await {
        Ok(report) => tracing::info!(
            "startup reconcile: staged_swept={} final_audit_swept={} failed={}",
            report.staged_swept,
            report.final_audit_swept,
            report.failed_dels.len(),
        ),
        Err(err) => tracing::warn!(
            "startup reconcile failed (non-fatal): {err}"
        ),
    }

    // ── Periodic spawn ──
    let repo = app_state.repository.clone();
    let interval_secs = std::env::var("RECONCILER_INTERVAL_SECS")
        .ok().and_then(|s| s.parse().ok()).unwrap_or(300);
    tokio::spawn(async move {
        let mut ticker = tokio::time::interval(
            std::time::Duration::from_secs(interval_secs)
        );
        ticker.tick().await; // burn the immediate first tick
        loop {
            ticker.tick().await;
            match repo.reconcile_orphan_tasks().await {
                Ok(report) => tracing::info!(
                    "periodic reconcile: staged_swept={} final_audit_swept={} failed={}",
                    report.staged_swept,
                    report.final_audit_swept,
                    report.failed_dels.len(),
                ),
                Err(err) => tracing::warn!(
                    "periodic reconcile failed: {err}"
                ),
            }
        }
    });

    // ── Build router and bind ──
    let router = build_router(app_state.clone());
    let listener = tokio::net::TcpListener::bind(...).await?;

    // ── Shutdown signal ──
    let shutdown_repo = app_state.repository.clone();
    axum::serve(listener, router)
        .with_graceful_shutdown(async move {
            tokio::signal::ctrl_c().await.ok();
            tracing::info!("shutdown signal received; running final reconcile");
            let _ = shutdown_repo.reconcile_orphan_tasks().await;
        })
        .await?;

    Ok(())
}
```

The shutdown reconcile runs *after* the listener stops accepting
new requests but *before* the process exits, so the final pass
operates against a quiescent keyspace.

---

## Test plan

### Backend integration tests

**File:** `Deferno/backend/src/repository/orphan_reconciler.rs`
(new). Add a `#[cfg(test)] mod tests` block.

**Style precedent:** existing repository tests in
[`repository/tasks.rs`](../../../../Deferno/backend/src/repository/tasks.rs)
that use `seed_state_and_user` and `#[serial]`.

**Tests to add:**

1. **`staging_sweep_drains_aged_keys`**
   Manually SET `staged:task:{uid}:{some-id}` with a known value.
   Wait `STAGE_RECONCILE_AGE_SECS + 1` seconds *(test-only:
   override the env var to 1s).* Call `reconcile_orphan_tasks`.
   Assert the key is DEL'd and `report.staged_swept == 1`.

2. **`staging_sweep_skips_fresh_keys`**
   Set the env-var override so the threshold is 30s. Manually
   SET a staging key. Immediately call `reconcile_orphan_tasks`
   (idle is 0). Assert the key still exists and
   `report.staged_swept == 0`.

3. **`final_audit_drops_unreferenced_task_blob`**
   Manually SET `task:{some-id}` with no entry in any
   `user:*:tasks` set. Call `reconcile_orphan_tasks`. Assert
   the key is DEL'd and `report.final_audit_swept == 1`.

4. **`final_audit_preserves_referenced_task`**
   Pre-create a task via the normal handler (so it exists in
   `user:{uid}:tasks` and `task:{id}`). Call
   `reconcile_orphan_tasks`. Assert the task still exists. The
   reconciler must not affect normal data.

5. **`final_audit_preserves_child_only_task`**
   Pre-create parent task X with child Y, then manually SREM Y
   from `user:{uid}:tasks` (simulating an inconsistency where
   Y is referenced via X's children array but not via the main
   set). Call `reconcile_orphan_tasks`. Assert Y's `task:{Y.id}`
   blob still exists — children-array references count as
   referenced.

6. **`reconciler_does_not_delete_referenced_tasks_under_concurrent_load`**
   Pre-create 50 tasks for user A. In parallel: spawn one task
   that calls `reconcile_orphan_tasks` repeatedly, and another
   that calls `get_all_tasks(user_A)` repeatedly. Assert
   `get_all_tasks` always returns 50 tasks throughout.

7. **`reconciler_is_idempotent`**
   Set up a staging-key orphan and a final-keyspace orphan.
   Call `reconcile_orphan_tasks` twice. Assert the first call
   returns `(staged_swept=1, final_audit_swept=1)`, the second
   returns `(staged_swept=0, final_audit_swept=0)`. No state
   corruption.

8. **`startup_hook_runs_before_router_binds`**
   Plant orphans in the keyspace. Boot the server (programmatic,
   in-process). Hit the `/health` endpoint immediately. Assert
   the orphans are gone — the startup hook ran before the
   router was reachable.

9. **`shutdown_hook_runs_final_reconcile`**
   Plant an orphan. Boot, then send SIGTERM (or trigger the
   shutdown future directly in test). Wait for the process to
   exit. Re-attach to Redis and confirm the orphan is gone.

10. **`reconciler_handles_unparseable_keys`**
    Manually SET a malformed key like `task:not-a-uuid`. Call
    `reconcile_orphan_tasks`. Assert the call returns Ok and
    the malformed key is left in place (no panic, no false-
    positive deletion).

---

## Backwards compatibility

This spec is purely additive. No existing API, schema, or
handler changes. The reconciler runs autonomously on a schedule
and never modifies data the user can observe via existing read
paths — the only state it changes is "orphan content blobs that
were already unreachable."

A deploy that ships this without the sibling specs is still
useful: the final-keyspace audit catches orphans that already
exist in any production deploy from any pre-existing source.

---

## Open questions

1. **Tuning `STAGE_RECONCILE_AGE_SECS` and
   `RECONCILER_INTERVAL_SECS`.** Defaults (1800s, 300s) are
   reasonable starting points. After the Create variant lands
   and produces real staging-key traffic, the metrics from
   `ReconcileReport` should inform recalibration.

2. **Audit-sweep cost at scale.** The pre-load HashSet of all
   referenced ids is O(total_tasks) memory. For a deploy with
   tens of millions of tasks, this becomes uncomfortable. The
   streaming alternative (per-user audit, one user at a time)
   is straightforward and can be added if memory pressure
   shows up. Not worth implementing speculatively in v1.

3. **Cluster-mode SCAN.** Single-instance Redis: SCAN walks the
   whole keyspace. Cluster mode: SCAN must be issued per shard
   and results merged. The reconciler module isolates this in
   one place (the SCAN helpers); a future cluster-mode adapter
   can wrap it without touching the audit logic. Not required
   for v1.

4. **Observability surface.** This spec emits structured tracing
   events on each reconcile. A future spec could add:
   - A Prometheus counter for staged-swept / audit-swept totals.
   - A gauge for "current orphan count" sampled at the start of
     each pass (before DEL).
   - A `/admin/reconcile` HTTP endpoint to trigger a sweep on
     demand for debugging.
   None of these are required for the correctness guarantee;
   they are operational sugar.
