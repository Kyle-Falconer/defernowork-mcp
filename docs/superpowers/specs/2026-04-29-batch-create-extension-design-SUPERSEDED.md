> # ⚠️ SUPERSEDED — DO NOT IMPLEMENT FROM THIS DOCUMENT ⚠️
>
> This spec was split on 2026-04-29 into three smaller, independently
> shippable specs. **Read those instead.** This file is kept for
> historical reference only; its design has been superseded — notably,
> the cleanup model is now reconciler-as-guarantee with a staging-key
> marker (`staged:task:{uid}:{newId}`), not the Tier 1 / Tier 2 / Tier 3
> best-effort scheme described below.
>
> **Read these instead, in dependency order:**
>
> 1. [`2026-04-29-batch-atomicity-refactor-design.md`](2026-04-29-batch-atomicity-refactor-design.md)
>    — four-phase atomicity model + staging-key contract (foundation)
> 2. [`2026-04-29-orphan-reconciler-design.md`](2026-04-29-orphan-reconciler-design.md)
>    — the cleanup guarantee (sweeps staging keys + audits final keyspace)
> 3. [`2026-04-29-batch-create-variant-design.md`](2026-04-29-batch-create-variant-design.md)
>    — `BatchOperation::Create` + nested wire format + MCP docstring
>
> Anything below this banner is **historical only**.

---

# `/tasks/batch` — `Create` operation extension — design

**Status:** SUPERSEDED (split into three sibling specs — see banner above)
**Date:** 2026-04-29
**Repos:** `Deferno` (Rust backend), `defernowork-mcp` (Python MCP server)
**Source of truth:** the Rust types in [`Deferno/backend/src/payloads.rs`](../../../../Deferno/backend/src/payloads.rs) and the handler in [`Deferno/backend/src/handlers/tasks.rs`](../../../../Deferno/backend/src/handlers/tasks.rs). The MCP `batch` tool docstring is descriptive, not authoritative.

---

## Why

Today `POST /tasks/batch` accepts only `update` and `move`
operations ([`payloads.rs:265-278`](../../../../Deferno/backend/src/payloads.rs)).
An MCP client that wants to create N tasks must issue N round-trips
through `POST /tasks`, none of which are atomic with each other —
if call #4 fails, calls #1-3 have already persisted as orphans the
caller doesn't know exist. Common batch flows (importing a
checklist, splitting a brain-dump, generating a daily plan from a
template, building a sub-tree of work breakdown items) all hit
this. The fix:

1. Extend `BatchOperation` with a `Create` variant.
2. Make the wire format **nested** so a single batch can express a
   subtree (parent + children + grandchildren).
3. Make the whole batch **truly atomic** — no partial-flush
   middle ground. Either every op in the batch lands or none do.

The wire format and the atomicity model are independent decisions:
nested JSON is an ergonomic choice, atomicity is a correctness
guarantee. This spec specifies both.

## API version contract

Purely additive within `v0.1`:

- `BatchOperation` gains a tag value `"create"`.
- `BatchCreateFields` carries an optional `children: Vec<BatchCreateFields>`
  array — recursive, encoding hierarchy in the JSON.
- `BatchResponse { tasks: Vec<Task> }` is unchanged. All created
  tasks (and any updated/moved tasks) appear flat in `tasks`. The
  client reconstructs the tree from each task's `parent_id` /
  `children` fields.

Per the additive-within-version rule from
[`2026-04-27-mcp-spec-driven-tests-design.md`](2026-04-27-mcp-spec-driven-tests-design.md),
no version bump is required. Older callers that emit only
`"op": "update"` / `"op": "move"` are unaffected.

## Goal

Extend `BatchOperation` with a `Create` variant whose wire format
expresses a tree, and tighten the batch handler so the entire
operation list (creates, updates, moves) commits or rolls back as
a single atomic unit.

### Success criteria

1. `BatchOperation::Create { fields: BatchCreateFields }`
   deserialises from a JSON object tagged `"op": "create"`.
2. `BatchCreateFields` carries the same fields as `CreateTaskPayload`
   plus a recursive `children: Vec<BatchCreateFields>` array.
   `parent_id` is allowed only at the top level; on a nested
   create, the parent is implicit.
3. The handler walks the create-tree depth-first, mints UUIDs,
   wires `parent_id` / `children` in memory, and stages the new
   task blobs to storage as **orphans** (written but not yet
   referenced by the user's task set, root order, or item-kind
   index).
4. After validation passes for every op (creates, updates, moves),
   the handler issues a single atomic commit that makes all
   staged orphans visible AND applies all in-memory mutations to
   pre-existing tasks. There is no observable middle state.
5. If validation fails at any step, every staged orphan is
   deleted before the handler returns. No mutation to pre-existing
   tasks has been written at this point (mutations are held
   in-memory until commit).
6. The MCP `batch` tool's docstring documents the nested format,
   the implicit-parent rule for nested creates, and the atomic
   guarantee.
7. RED tests cover the nested create round-trip, the
   atomic-rollback path (a failure mid-batch leaves no trace),
   and the implicit-parent wiring.

## Non-goals

- **Nesting other op types.** Only `Create` carries `children`.
  `Update` and `Move` do not nest. If a batch needs to attach a
  pre-existing task to a freshly-created parent, that is a top-
  level `Move` whose `new_parent_id` is the just-created task's
  UUID — but since the caller doesn't know that UUID until the
  response, this is a two-batch flow. Documented limitation.
- **Recurring task creation via batch.** The Task payloads reject
  `recurrence` / `recurring_type` ([`payloads.rs:84-98`](../../../../Deferno/backend/src/payloads.rs));
  recurring items go through `POST /items/:id/convert`. Batch
  inherits this rejection at every level of the create-tree.
- **Cross-user batches.** A batch operates as a single user — the
  authenticated `CurrentUser`. No change.
- **Reordering siblings during create.** Children land in the
  order they appear in the JSON array. There is no `position`
  field on a nested create. (`Move` still has `position` for
  reordering existing tasks.)

---

## Architecture

The change is a recursive struct on the wire, a tree walk in the
handler, and a refactor of the repository flush layer to support
**stage / wire / commit / cleanup-on-failure** as four discrete
phases instead of a single in-memory cache + best-effort flush.

```
Deferno/backend/src/
  payloads.rs                   # add BatchCreateFields (recursive) + BatchOperation::Create variant
  handlers/tasks.rs             # batch_tasks gains a Create match arm + tree-walk pre-validation
  models.rs                     # BatchOp::Create variant on the repository-level enum
  repository/batch.rs           # rewrite batch_operations around stage/wire/commit/cleanup
  repository/tasks.rs           # expose pipeline-queueing variants of save_task / index_task / etc.
  repository/mod.rs             # expose pipeline-queueing variants of root_order_* and user_tasks_*

defernowork-mcp/src/defernowork_mcp/
  tools/tasks.py                # update batch() docstring only

defernowork-mcp/tests/
  test_batch_create_payload.py  # NEW — respx-mocked round-trips for flat + nested + mixed batches
```

The MCP `client.py:batch` method already takes
`list[dict[str, Any]]` and ships it verbatim
([`client.py:246-247`](../../../../defernowork-mcp/src/defernowork_mcp/client.py)) —
no client-side change is needed. Nested JSON is just nested dicts
on the Python side.

---

## 1. Rust types — nested `BatchOperation::Create`

### Current shape

```rust
// Deferno/backend/src/payloads.rs:265-278
#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "lowercase")]
pub enum BatchOperation {
    Update {
        task_id: Uuid,
        #[serde(flatten)]
        fields: BatchUpdateFields,
    },
    Move {
        task_id: Uuid,
        new_parent_id: Option<Uuid>,
        position: Option<usize>,
    },
}
```

The `#[serde(tag = "op", rename_all = "lowercase")]` attribute
puts serde in **internally-tagged enum** mode: each variant
deserialises from a JSON object that contains a string field
named `op` whose value matches the lowercased variant name.
Adding a new variant `Create` adds the tag value `"create"`.

### Proposed shape

```rust
// Deferno/backend/src/payloads.rs (additions)

#[derive(Debug)]
pub struct BatchCreateFields {
    pub title: String,
    pub description: String,
    pub labels: Option<Vec<String>>,
    pub assignee: Option<Uuid>,
    pub complete_by: Option<DateTime<Utc>>,
    pub productive: Option<f64>,
    pub desire: Option<f64>,
    pub mood_start: Option<MoodVector>,
    pub mood_finish: Option<MoodVector>,
    /// Only valid on a top-level Create. On nested creates, the
    /// parent is implicit (the enclosing Create's freshly-minted
    /// UUID); supplying it on a nested Create is rejected at
    /// validation time with a 400.
    pub parent_id: Option<Uuid>,
    /// Nested creates whose parent_id will be set to this Create's
    /// freshly-minted UUID server-side. Recurses to arbitrary
    /// depth. Children appear in the response in the order given.
    pub children: Vec<BatchCreateFields>,
}

// Deserialize impl uses the same two-phase shim as
// CreateTaskPayload (payloads.rs:114-163): deserialize to
// serde_json::Value, call reject_recurrence_hint_fields, then
// re-deserialize into a private shim. The shim's `title` field
// has no serde(default) (required); `description` defaults to ""
// like CreateTaskPayload; `children` defaults to an empty Vec so
// leaf creates can omit it. The recurrence-hint rejection runs at
// every level of the tree because each nested BatchCreateFields
// goes through this same impl recursively.

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "lowercase")]
pub enum BatchOperation {
    Update {
        task_id: Uuid,
        #[serde(flatten)]
        fields: BatchUpdateFields,
    },
    Move {
        task_id: Uuid,
        new_parent_id: Option<Uuid>,
        position: Option<usize>,
    },
    Create {
        #[serde(flatten)]
        fields: BatchCreateFields,
    },
}
```

`BatchCreateFields` is a struct (not inlined on the variant)
because (a) `reject_recurrence_hint_fields` attaches to a struct's
`Deserialize` impl, not an enum variant; (b) the struct is what
recurses through `children`. The variant uses `#[serde(flatten)]`
so the wire format puts `op`, `title`, `description`, ... and
`children` at the same level.

### Wire format

A flat single-create batch (the simplest case):

```json
{
  "operations": [
    { "op": "create", "title": "Inbox" }
  ]
}
```

A subtree in one batch:

```json
{
  "operations": [
    {
      "op": "create",
      "title": "Q3 launch",
      "description": "Comms + assets + analytics",
      "labels": ["q3"],
      "children": [
        {
          "title": "Draft brief",
          "description": "Outline the messaging"
        },
        {
          "title": "Assets",
          "children": [
            { "title": "Hero image" },
            { "title": "Email header" }
          ]
        },
        { "title": "Analytics dashboard" }
      ]
    }
  ]
}
```

Nested children carry **no `op` field** — the implicit op for
anything inside a `children: [...]` array is `create`. Updates and
moves cannot nest; they only appear at the top level.

A mixed-op batch (create a tree, update an existing task, move
another existing task) is one wire body:

```json
{
  "operations": [
    { "op": "create", "title": "Project", "children": [{ "title": "Subtask" }] },
    { "op": "update", "task_id": "11111111-...", "status": "in-progress" },
    { "op": "move",   "task_id": "22222222-...", "new_parent_id": null }
  ]
}
```

### Payload-level invariants

- **`title` is required** at every level (no `#[serde(default)]`).
  Matches `CreateTaskPayload`.
- **`description` defaults to `""`** at every level. Matches
  `CreateTaskPayload` ([`payloads.rs:127`](../../../../Deferno/backend/src/payloads.rs)).
- **`children` defaults to `Vec::new()`.** Leaf creates can omit
  the array; the deserialiser fills it in.
- **Recurrence keys are rejected at every level.** Each
  `BatchCreateFields` runs `reject_recurrence_hint_fields` —
  including nested ones. A `recurrence` key on a grandchild 422s
  the whole batch.
- **`parent_id` is rejected on nested creates.** The pre-
  validation walk in the handler returns 400 with message
  *"operation N: parent_id is only valid on a top-level create
  (the parent is implicit for nested creates)"* if it appears
  inside a `children` entry. The deserialiser permits it
  syntactically (because `BatchCreateFields` is one struct that
  serves both top-level and nested cases); the handler enforces
  the position-dependent rule.
- **Top-level `parent_id`, when present, must be a real `Uuid`.**
  No string tokens, no in-batch references. Type-enforced.
- **Cross-batch references are not supported.** An `Update` or
  `Move` op in the same batch cannot name a freshly-created task
  by id, because the caller does not know the id until the
  response returns. To attach a pre-existing task to a freshly-
  created parent, run two batches: create-batch first, then
  move-batch with the now-known UUIDs.

The trade-off: nesting handles 95% of subtree-creation use cases
ergonomically; the residual 5% (attaching a pre-existing task to
a brand-new parent in one round-trip) is left to a two-batch
flow. The MCP docstring documents this.

---

## 2. Atomicity model — stage / wire / commit / cleanup

### What the existing handler actually guarantees today

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
atomicity, and does so without depending on any single
datastore's transaction primitives.

### The model: orphan-stage + atomic visibility flip

The atomicity guarantee is delivered by separating *content
write* (heavy, may fail, idempotent) from *visibility flip*
(small, single atomic instruction, what the user observes).

**Phase 1 — stage.** For every new task in the batch (every
`Create` op, including all nested children), mint a UUID and
write the encrypted task blob to its content key
(`task:{newId}`). The blob includes the freshly-wired
`parent_id` and `children` arrays — the in-memory tree is fully
formed before any blob is written.

After phase 1, the new tasks exist in storage as **orphans**:

- `task:{newId}` is set in Redis.
- `user:{uid}:tasks` does NOT yet contain `newId`. So
  `get_user_tasks` cannot see it.
- `user:{uid}:item_kinds` does NOT yet contain `newId`. So the
  cross-kind lookups cannot see it.
- `user:{uid}:root_order` does NOT yet contain `newId`. So the
  task list view cannot see it.
- The search index does NOT yet contain `newId`. So search
  cannot find it.

The user observes nothing has changed. The task data exists in
storage but is unreachable through any read path.

**Phase 2 — wire (in memory only).** For every `Update` and
`Move` op, apply the mutation against an in-memory copy of the
existing task. For any existing task that gains a child as the
parent of a top-level `Create` (`fields.parent_id == Some(pid)`
where `pid` is a real, pre-existing task), update the parent's
in-memory `children` array to include the new child's id.
**Nothing is written to storage in this phase.** All mutations
are buffered.

**Phase 3 — validate.** The buffered mutations are inspected for
contract violations:

- task-not-found, parent-not-owned, status cascade rejections,
  cycle checks for `Move`, recurrence-key presence, etc.
- `parent_id` on nested creates: rejected here.

If validation fails, jump to Phase 4 (cleanup) and return the
appropriate `ApiError`.

**Phase 4 — commit.** A single atomic block performs:

1. For each new task: SADD `user:{uid}:tasks` newId, HSET
   `user:{uid}:item_kinds` newId "task". This is the visibility
   flip. After this point, `get_user_tasks` returns the new task.
2. For each new top-level *root* task (no parent_id): RPUSH/LUA-
   insert `user:{uid}:root_order` newId.
3. For each existing task with buffered mutations (Updates, Moves,
   parent-gained-child): SET its `task:{id}` blob to the new
   in-memory state.
4. For each new and each mutated task: HSET its search-index row.
5. For Move ops that change root membership: ZADD/LREM
   `user:{uid}:root_order`.

On Redis today, this block is a single `redis::pipe().atomic()`
(MULTI/EXEC) — the pattern already used by [`comments.rs:62-63`](../../../../Deferno/backend/src/repository/comments.rs),
[`tasks.rs:99-104`](../../../../Deferno/backend/src/repository/tasks.rs),
and across `chores.rs` / `events.rs` / `habits.rs`. EXEC either
runs all queued commands or none.

On a hypothetical future datastore that does not support
multi-key transactions, the same model degrades cleanly:

- The visibility flip is `SADD user:{uid}:tasks` plus its
  siblings. If only single-key atomicity is available, replace
  this with a *single-key* manifest: one Redis hash
  `user:{uid}:task_set` whose value is the JSON-serialised set
  of task ids. The atomic instruction becomes a single SET on
  that key, computed from the prior value plus the additions.
  Mutations to existing tasks similarly stage to
  `task:shadow:{id}` and are committed by RENAME.

This degraded path is **not implemented in v1.** v1 uses
MULTI/EXEC. The model is described abstractly here so that a
future migration to a different store is a refactor of one phase,
not a redesign.

**Phase 5 — cleanup-on-failure.** If validation (Phase 3) or
commit (Phase 4) fails, every staged orphan must be removed from
storage. The orphans are invisible to the user, but
*invisibility is not deletion* — left in place, they accumulate
forever and waste keyspace. Cleanup is mandatory.

Cleanup is a three-tier guarantee:

1. **Tier 1 — synchronous DEL with bounded retry.** Inside the
   handler's error path, attempt `DEL task:{newId}` for every
   staged orphan. Each DEL gets up to 3 attempts with a short
   exponential backoff (e.g. 0ms / 50ms / 200ms). The vast
   majority of failures (transient network blips, momentary
   Redis pressure) are absorbed here.

2. **Tier 2 — persistent pending-cleanup set.** If a Tier 1 DEL
   fails after all retries, the orphan id is appended to a
   per-user Redis set `cleanup:orphan_tasks:{uid}` via SADD.
   The handler returns its original error to the caller; the
   orphan is recorded as work for the reconciler to finish.
   The SADD itself is a single Redis op — if even *that* fails,
   the orphan id is logged at ERROR level with full context so
   it surfaces in observability and ops can drain it manually.
   This is the only path where an orphan can survive past the
   handler's return; it is bounded, observable, and recoverable.

3. **Tier 3 — reconciler.** A background task drains
   `cleanup:orphan_tasks:{uid}` for every user. It runs (a) on
   server startup (so a crash mid-cleanup is recovered before
   the server begins serving requests) and (b) on a periodic
   timer (configurable; default every 5 minutes). For each id
   in the set, attempt `DEL task:{id}`. On success, SREM the id
   from the set. On failure, leave the id in the set for the
   next sweep. The reconciler also performs a defense-in-depth
   audit: SCAN for `task:*` keys whose id is not present in any
   `user:{uid}:tasks` set and is not present in any persisted
   task's `children` array — these are unaccounted-for orphans
   from any source, not just batch failures, and they too get
   DEL'd.

After a successful Tier 1 + Tier 2 cycle, the user's data is
exactly what it was before the batch began. After a successful
Tier 3 sweep, the keyspace is exactly what it was before the
batch began. **No orphan ever persists across a reconciler
cycle.** "Dangling disconnected branches or nodes" are
impossible: orphans never had pointers into the user-visible
task graph in the first place (Phase 1 wrote only `task:{newId}`
content keys, with no entry in `user:{uid}:tasks`,
`user:{uid}:item_kinds`, or `user:{uid}:root_order`), and the
reconciler removes the orphan content blobs themselves.

### The reconciler — concrete shape

A new module `Deferno/backend/src/repository/orphan_reconciler.rs`
exposes:

```rust
impl TaskRepository {
    /// Drain cleanup:orphan_tasks:{uid} for every user, plus
    /// audit-sweep for unaccounted task:* keys. Idempotent. Safe
    /// to call concurrently with batch_operations because every
    /// step is per-key and the orphan ids it operates on cannot
    /// be referenced by any user-visible structure.
    pub async fn reconcile_orphan_tasks(&self) -> Result<ReconcileReport>;
}

pub struct ReconcileReport {
    pub drained_pending: usize,        // how many ids the pending set held that we DEL'd
    pub audit_swept: usize,            // how many unaccounted task:* keys we DEL'd in audit
    pub still_pending: Vec<Uuid>,      // ids whose DEL still failed — surface for observability
}
```

Wiring into the server lifecycle:

- **Startup hook** in `Deferno/backend/src/main.rs` calls
  `reconcile_orphan_tasks().await` once before the Axum router
  binds to a port. A startup failure here is logged but
  non-fatal — the server still boots and the periodic sweep
  will retry.
- **Periodic task** spawned at startup via
  `tokio::spawn(async move { loop { sleep; reconcile; } })` with
  the interval configured through `RECONCILER_INTERVAL_SECS` env
  var (default 300).
- **Shutdown signal** (SIGTERM / Ctrl-C handler) triggers one
  final reconcile pass before the server exits, so a clean
  shutdown leaves no pending work.

The audit-sweep portion uses Redis `SCAN` (not `KEYS`, which
blocks the server) over `task:*` and joins each id against the
per-user `tasks` set + `children` arrays. SCAN is constant-
memory and progress-resumable; the sweep can be interrupted and
restarted without losing place.

### Why this is portable

The model has three pieces:

1. **Stage** — a sequence of independent writes to fresh keys
   that no read path observes.
2. **Commit** — a single atomic instruction (or block) that
   makes the tree visible and applies in-memory mutations.
3. **Cleanup** — a best-effort sweep of the staged keys on
   failure.

The portable invariant is *"phase-2 writes are not visible until
the phase-4 instruction completes."* Whether the phase-4
instruction is MULTI/EXEC, a SQL transaction, or a manifest swap
is implementation-specific. The wire format, the validation
logic, the in-memory tree walk, and the test plan are all
independent of which atomicity primitive the underlying store
provides.

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
    // can find them. Holding this outside the try-block means the
    // catch arm has access regardless of which phase failed.
    let mut staged_orphans: Vec<Uuid> = Vec::new();

    let result: Result<BatchResult> = async {
        // ── Phase 1: stage new tasks ────────────────────────
        // Pre-walk every Create (including nested children) and
        // mint a UUID. Build a Vec<Task> with parent_id /
        // children fully wired. Write each blob to task:{newId}
        // — orphans, no user-set membership, no root_order, no
        // item_kind. ensure_cached() loads existing tasks the
        // batch will mutate.
        let staged_creates = self
            .stage_create_tree(&mut conn, &ops, user_id, dek, &mut staged_orphans)
            .await?;

        // ── Phase 2: wire existing-task mutations in memory ──
        // Apply every Update and Move op to an in-memory cache
        // of the affected pre-existing tasks. Add children
        // arrays for parents of top-level Creates. NOTHING
        // writes here.
        let mutations = self
            .wire_existing_mutations(&mut conn, &ops, &staged_creates, user_id, dek)
            .await?;

        // ── Phase 3: validate ───────────────────────────────
        // Run every remaining check: parent_id on nested
        // creates, owner checks for top-level parent_ids, status
        // cascades inside Update, cycle checks inside Move,
        // recurrence-hint scan. Returns Err on the first failure.
        self.validate_batch(&staged_creates, &mutations, user_id)?;

        // ── Phase 4: commit (single atomic block) ───────────
        // One pipe.atomic() block queues every visibility-flip
        // instruction and every existing-task mutation write.
        let mut pipe = redis::pipe();
        pipe.atomic();
        for new_task in &staged_creates.tasks {
            queue_visibility_flip(&mut pipe, user_id, new_task);
        }
        for mutated in &mutations.tasks {
            queue_blob_write(&mut pipe, mutated, dek)?;
        }
        for root_op in &mutations.root_order_ops {
            queue_root_order_op(&mut pipe, user_id, root_op);
        }
        pipe.query_async::<()>(&mut conn).await?;

        // ── Search index update (non-fatal if it fails) ─────
        // RediSearch HSETs are inside the same Redis instance,
        // so they CAN go in the same pipe.atomic() — and v1
        // does so. Documented separately because the failure
        // semantics deserve their own paragraph: if the index
        // update queues fail, the EXEC fails, the whole commit
        // is rolled back, cleanup runs.

        Ok(BatchResult {
            tasks: collect_response_tasks(&staged_creates, &mutations),
        })
    }
    .await;

    // ── Phase 5: cleanup-on-failure ─────────────────────────
    if result.is_err() && !staged_orphans.is_empty() {
        for orphan_id in &staged_orphans {
            // Best-effort delete. Errors here are logged and
            // ignored — the orphan is invisible regardless.
            let _ = redis::cmd("DEL")
                .arg(Self::task_key(*orphan_id))
                .query_async::<()>(&mut conn)
                .await;
        }
    }

    result
}
```

The functions `stage_create_tree`, `wire_existing_mutations`,
`validate_batch`, `queue_visibility_flip`, `queue_blob_write`,
`queue_root_order_op`, `collect_response_tasks` are private
helpers on `TaskRepository`. Their bodies are not specified in
detail here — they correspond directly to the phase descriptions
above. The implementer should mirror the existing helper shapes
in [`repository/batch.rs`](../../../../Deferno/backend/src/repository/batch.rs)
(`apply_update`, `apply_move`) for code style.

### Tree walk for `stage_create_tree`

Depth-first, pre-order. For each `BatchOperation::Create` at the
top level:

1. Mint `new_id = Uuid::new_v4()`.
2. Build `Task::new_with_id(new_id, fields)` setting `parent_id`
   from `fields.parent_id` (top-level only — see invariant) and
   `created_by = Some(user_id)`.
3. For each child in `fields.children`:
   - Mint `child_id`.
   - Build child Task with `parent_id = Some(new_id)`,
     `created_by = Some(user_id)`.
   - Recurse into grandchildren (steps 3-4 repeat).
   - Append `child_id` to the parent Task's `children` array.
4. Encrypt and `SET task:{new_id} <encrypted_blob>` — orphan
   write.
5. Push `new_id` onto `staged_orphans`.

Order matters: the child task blobs must be written before their
parent's blob, so when the parent is written its `children`
array is already correct. Equivalently, build the entire
in-memory subtree first (purely structural, no I/O) and then
write blobs in any order.

### Handler-side changes in `batch_tasks`

[`tasks.rs:430-544`](../../../../Deferno/backend/src/handlers/tasks.rs)
already does a per-op pre-loop that builds the `Vec<BatchOp>`.
The new `Create` arm is small:

```rust
// Inside the for (idx, op) in payload.operations.into_iter().enumerate() loop:
BatchOperation::Create { fields } => {
    // For top-level Create with explicit parent_id, mirror Move's
    // owner check (tasks.rs:496-513). The standalone POST /tasks
    // handler does NOT do this (tasks.rs:37-66) — batch tightens
    // it deliberately so a single request cannot build a tree
    // under another user's task.
    if let Some(pid) = fields.parent_id {
        let parent = state
            .repository
            .get_task(pid, &user.dek)
            .await
            .map_err(internal_error)?
            .ok_or_else(|| ApiError::new(
                StatusCode::NOT_FOUND,
                format!("operation {idx}: parent task not found"),
            ))?;
        if parent.created_by != Some(user.id) {
            return Err(ApiError::new(
                StatusCode::NOT_FOUND,
                format!("operation {idx}: parent task not found"),
            ));
        }
    }
    ops.push(BatchOp::Create { fields });
}
```

The recursive nested-create validation (rejecting `parent_id` on
non-root creates, recursing into `children`) lives inside the
repository's tree walk, not in the handler. The handler's only
batch-level concern is owner checks for tasks the request names
explicitly (top-level `parent_id`, `Update::task_id`,
`Move::task_id`, `Move::new_parent_id`).

### How tasks reach `BatchResponse`

`BatchResponse { tasks: Vec<Task> }` ([`payloads.rs:285-288`](../../../../Deferno/backend/src/payloads.rs))
is unchanged. The flat `tasks` array contains:

- Every newly-created task (every node of every create-tree),
  with `id`, `parent_id`, and `children` populated so the client
  can rebuild the tree shape if needed.
- Every updated task.
- Every moved task.
- Every existing task whose `children` array was mutated because
  a top-level Create attached to it.

Order is not guaranteed. Tests must not assert order. Clients
that need a tree-shaped response can group by `parent_id`.

### Concurrency note

Two concurrent batches by the same user can race in Phase 1
(both stage their orphans), then serialise on Phase 4 (each
EXEC commits independently). This is fine for create-only
batches. For batches that *update the same existing task*, the
last EXEC wins — there is no row-level locking. This matches the
existing single-task `update_task` semantics ([`repository/tasks.rs::update_task`](../../../../Deferno/backend/src/repository/tasks.rs))
and is not made worse by this spec. If lost-update-detection is
needed, that is a separate spec adding `If-Match` ETags.

---

## 3. MCP `batch` tool docstring update

### Current docstring

[`defernowork-mcp/src/defernowork_mcp/tools/tasks.py:317-336`](../../../../defernowork-mcp/src/defernowork_mcp/tools/tasks.py):

```python
"""Execute multiple task operations atomically in a single call.

``operations`` is a list of operation objects. Each must have an
``op`` field (``"update"`` or ``"move"``) and a ``task_id``.
...
"""
```

### Replacement docstring (exact prose)

```python
"""Execute multiple task operations atomically in a single call.

``operations`` is a list of operation objects. Each must have an
``op`` field — one of ``"create"``, ``"update"``, or ``"move"``.

Create operations require ``title`` (string) and accept these
optional fields at the top level: ``description`` (string,
defaults to empty), ``labels`` (list of strings), ``assignee``
(UUID), ``complete_by`` (ISO-8601 UTC timestamp), ``productive``
(float in [0, 1]), ``desire`` (float in [0, 1]), ``parent_id``
(UUID of an existing task this caller owns, or omit for a
root-level task), and ``children`` (a list of child create
objects).

A create can carry a ``children`` array to express a subtree.
Each entry in ``children`` is a create object with the same
shape as a top-level create, except that it must NOT carry an
``op`` field (the op is implicit) and it must NOT carry
``parent_id`` (the parent is the enclosing create, which only
gets its UUID at server-commit time). ``children`` can nest to
arbitrary depth. Example::

    {"op": "create", "title": "Project", "children": [
        {"title": "Subtask"},
        {"title": "Branch", "children": [
            {"title": "Leaf"}
        ]}
    ]}

Update operations require ``task_id`` and accept the same fields
as ``update_task``.

Move operations require ``task_id`` and accept ``new_parent_id``
(UUID or null for root) and an optional ``position``.

Atomicity: the whole batch — every create, update, and move —
commits or rolls back as one. New tasks are staged invisibly
first; the user does not see any new task until every operation
in the batch has passed validation. If anything fails, no
operation in the batch is observable. Recurrence is not
supported on Tasks; passing ``recurrence`` or ``recurring_type``
at any level (including inside ``children``) 422s the batch.

Ops cannot reference tasks created earlier in the same batch by
``task_id`` — the caller does not know the new UUIDs until the
response returns. To attach a pre-existing task to a brand-new
parent, run two batches: a create-batch first, then a move-batch
once the new UUIDs are known.

On success returns ``{"tasks": [...]}``, the flat list of all
created, updated, and moved tasks. Each task carries its
``parent_id`` and ``children`` so the client can rebuild the
tree shape. Response order is not guaranteed.
"""
```

### MCP client signatures

Unchanged. `@mcp.tool() async def batch(operations, ctx)` and
`DefernoClient.batch(operations)` already accept
`list[dict[str, Any]]` and pass the body verbatim
([`tools/tasks.py:337-342`](../../../../defernowork-mcp/src/defernowork_mcp/tools/tasks.py),
[`client.py:246-247`](../../../../defernowork-mcp/src/defernowork_mcp/client.py)).
Nested dicts on the Python side serialise to nested JSON without
extra plumbing.

---

## 4. Test plan

### 4a. MCP — RED tests, respx-mocked round-trip

**File:** `defernowork-mcp/tests/test_batch_create_payload.py` (new)

**Style precedent:** `defernowork-mcp/tests/test_create_task_payload.py`
(monkeypatched `_get_client_async`, registered-tool lookup, one
`@respx.mock` per test).

The MCP layer is a pass-through: these tests verify the request
body shape the MCP ships, not backend behaviour. End-to-end
proof of atomicity / orphan cleanup / nested wiring lives in the
backend integration tests in 4b.

```python
"""Regression tests for the JSON body the MCP ``batch`` tool ships
when given ``create`` operations, including nested trees."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from defernowork_mcp import server as srv
from defernowork_mcp.client import DefernoClient


BASE = "http://test:3000/api"
NEW_ID = "00000000-0000-0000-0000-000000000010"
PARENT_ID = "00000000-0000-0000-0000-000000000020"

BATCH_RESPONSE_ENVELOPE = {
    "version": "0.1",
    "data": {
        "tasks": [
            {
                "id": NEW_ID,
                "title": "Task",
                "status": "open",
                "actions": [{"kind": "Created"}],
                "date_created": "2026-04-29T00:00:00Z",
                "parent_id": None,
                "children": [],
            }
        ]
    },
    "error": None,
}


@pytest.fixture
def fastmcp(monkeypatch):
    async def _stub_get_client_async(ctx=None):
        return DefernoClient(base_url=BASE, token="test-token")
    monkeypatch.setattr(srv, "_get_client_async", _stub_get_client_async)
    monkeypatch.setattr(srv, "_http_transport_mode", False)
    return srv.create_server()


def _registered_tool(mcp, name):
    tools = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
    for attr in ("_tools", "tools"):
        tool_map = getattr(tools, attr, None)
        if isinstance(tool_map, dict) and name in tool_map:
            return tool_map[name]
    raise LookupError(f"tool {name!r} not registered")


async def _invoke(tool, **kwargs):
    fn = getattr(tool, "fn", None) or tool
    return await fn(**kwargs)


@respx.mock
@pytest.mark.asyncio
async def test_batch_flat_create_round_trips(fastmcp):
    """A single flat create ships op=create, title, no children key."""
    route = respx.post(BASE + "/tasks/batch").mock(
        return_value=httpx.Response(200, json=BATCH_RESPONSE_ENVELOPE)
    )
    tool = _registered_tool(fastmcp, "batch")
    await _invoke(
        tool,
        operations=[{"op": "create", "title": "Inbox"}],
    )
    body = json.loads(route.calls.last.request.content)
    assert body == {"operations": [{"op": "create", "title": "Inbox"}]}


@respx.mock
@pytest.mark.asyncio
async def test_batch_nested_create_round_trips(fastmcp):
    """A subtree (parent + 2 children + 1 grandchild) ships nested."""
    route = respx.post(BASE + "/tasks/batch").mock(
        return_value=httpx.Response(200, json=BATCH_RESPONSE_ENVELOPE)
    )
    tool = _registered_tool(fastmcp, "batch")
    await _invoke(
        tool,
        operations=[{
            "op": "create",
            "title": "Project",
            "children": [
                {"title": "Subtask 1"},
                {"title": "Branch", "children": [{"title": "Leaf"}]},
            ],
        }],
    )
    body = json.loads(route.calls.last.request.content)
    op = body["operations"][0]
    assert op["op"] == "create"
    assert op["title"] == "Project"
    assert len(op["children"]) == 2
    assert op["children"][0] == {"title": "Subtask 1"}
    assert op["children"][1]["title"] == "Branch"
    assert op["children"][1]["children"] == [{"title": "Leaf"}]
    # The implicit-op rule: nested children carry no "op" key.
    assert "op" not in op["children"][0]
    assert "op" not in op["children"][1]
    assert "op" not in op["children"][1]["children"][0]


@respx.mock
@pytest.mark.asyncio
async def test_batch_create_with_top_level_parent_id_round_trips(fastmcp):
    """Top-level parent_id reaches the backend verbatim; nested creates
    inside still don't carry one."""
    route = respx.post(BASE + "/tasks/batch").mock(
        return_value=httpx.Response(200, json=BATCH_RESPONSE_ENVELOPE)
    )
    tool = _registered_tool(fastmcp, "batch")
    await _invoke(
        tool,
        operations=[{
            "op": "create",
            "title": "Subtree under existing parent",
            "parent_id": PARENT_ID,
            "children": [{"title": "Leaf"}],
        }],
    )
    body = json.loads(route.calls.last.request.content)
    op = body["operations"][0]
    assert op["parent_id"] == PARENT_ID
    assert "parent_id" not in op["children"][0]
```

A fourth test, **`test_batch_mixed_ops_round_trip`**, follows
the same shape: send a batch of `[create-with-children,
update, move]` and assert the request body's `op` field
sequence is `["create", "update", "move"]` and that the create's
`children` array survives.

**Expected RED behaviour against today's main:** all four
tests pass at the MCP layer (pass-through serialisation works
today) but a real backend would 422 because `BatchOperation`
has no `Create` variant. The MCP-layer tests therefore prove
*shape*, not backend acceptance — the real verification is in
the backend tests below.

### 4b. Backend integration tests

**File:** `Deferno/backend/src/handlers/tasks.rs` (existing). Add
a new `#[cfg(test)] mod batch_create_tests` block after the
existing `import_version_tests` block at line 814.

**Style precedent:** `delete_task_tests` at [`tasks.rs:760-812`](../../../../Deferno/backend/src/handlers/tasks.rs)
(uses `seed_state_and_user`, `#[serial]` ordering, `axum::Json`).

**Tests to add:**

1. **`batch_create_persists_flat_task`**
   One-op batch: `op=create`, `title="t"`. Assert response
   contains one task; `get_all_tasks` lists it; the new task's
   `parent_id` is `None`.

2. **`batch_create_persists_subtree_in_one_call`**
   *(The tree test the user explicitly asked for.)*
   One-op batch with a parent and two children, one of which has
   a grandchild (4 new tasks total). Assert:
   - Response contains all 4 tasks.
   - The parent's `children` array has 2 entries; one of those
     children has 1 grandchild in its `children` array.
   - Each child's `parent_id` points at its parent's id.
   - `get_all_tasks` lists all 4.

3. **`batch_create_subtree_under_existing_parent`**
   Pre-create task X. One-op batch: `op=create`,
   `parent_id=X.id`, two children. Assert:
   - Response contains the new top-level task and 2 children.
   - X's `children` (re-fetched) now contains the new top-level
     task's id.
   - The new top-level task's `parent_id == X.id`.

4. **`batch_create_rejects_unowned_parent`**
   User A pre-creates a task. Run batch as user B with
   `op=create`, `parent_id=A_task.id`. Assert 404 with message
   *"operation 0: parent task not found"* AND no new task
   exists for either user.

5. **`batch_create_rejects_parent_id_on_nested`**
   Send `op=create` with a child that carries `parent_id`. Assert
   400 with message *"operation 0: parent_id is only valid on
   a top-level create"*. AND `get_all_tasks` is unchanged.

6. **`batch_create_rejects_recurrence_at_every_depth`**
   Three sub-tests:
   - `recurrence` on the top-level create → 422.
   - `recurrence` on a child → 422.
   - `recurrence` on a grandchild → 422.
   In all three, no new task persists.

7. **`batch_validation_failure_rolls_back_staged_orphans`**
   *(The atomicity test the user explicitly asked for.)*
   Three-op batch: op #0 creates task A, op #1 creates a subtree
   B + C + D, op #2 attempts to update a non-existent task id.
   Assert:
   - The call returns `404 NOT FOUND`.
   - `get_all_tasks` does NOT contain A, B, C, or D.
   - **Zero `task:*` keys exist for any of the four would-be
     IDs.** Test via a `count_orphan_task_keys(user_id)` helper
     added under `#[cfg(test)] pub` in `repository/mod.rs` that
     SCANs `task:*` and joins against `user:{uid}:tasks`.
   - **The pending-cleanup set
     `cleanup:orphan_tasks:{user_id}` is empty.** Tier 1
     synchronous cleanup succeeded; nothing should escalate.

7a. **`batch_failure_with_redis_blip_persists_then_reconciler_drains`**
   *(Proves the Tier 2 → Tier 3 path works.)*
   Inject a fault that makes the synchronous cleanup DEL fail
   for one specific orphan id (e.g. wrap the connection in a
   test-only adapter that returns an error on the first DEL of
   a configured id, then succeeds). Run a batch that fails
   validation. Assert:
   - The call returns the validation error to the caller.
   - The orphan id appears in `cleanup:orphan_tasks:{user_id}`.
   - One stale `task:{id}` blob still exists in Redis.
   - Then call `repository.reconcile_orphan_tasks().await`.
   - Assert: the pending set is now empty AND no orphan
     `task:*` keys remain AND `ReconcileReport.drained_pending
     == 1`.

7b. **`reconciler_audit_sweep_catches_externally_orphaned_tasks`**
   *(Proves the defense-in-depth audit works for orphans the
   handler never knew about — e.g. from a process that crashed
   before it could write to the pending set.)* Manually write
   a `task:{id}` blob via raw Redis with no entry in any user's
   `user:{uid}:tasks` set and no `children`-array reference.
   Run `reconcile_orphan_tasks()`. Assert:
   - The orphan blob is DEL'd.
   - `ReconcileReport.audit_swept == 1`.
   - `ReconcileReport.still_pending == []`.

8. **`batch_create_update_move_in_one_call_succeeds`**
   *(The mixed-op test the user explicitly asked for.)*
   Pre-create tasks X and Y. Run batch:
   - op #0: create new tree N1 → [N2 → N3]
   - op #1: update X's status to `in-progress`
   - op #2: move Y to be a child of X
   Assert: response contains N1, N2, N3, X, Y; X's status is
   `in-progress`; X's `children` contains Y.id and N1.id is NOT
   in X's children (N1 is root); N1.children == [N2.id]; etc.

9. **`batch_concurrent_create_does_not_leak_orphans`**
   Spawn two `tokio::spawn`'d batch_operations calls that both
   create a 3-task subtree for the same user. Await both. Assert
   `get_user_tasks(user_id)` contains all 6 new tasks. Assert
   the orphan-count helper from test 7 reports zero.

**Search-index assertions are out of scope for these tests** —
search index correctness is covered by
`repository/search.rs`'s own test suite. The atomicity tests
above only verify the user-set membership / parent-children
wiring / orphan-key absence, which is what the atomicity
guarantee actually delivers to callers.

### 4c. No docstring test

Docstring tests test the test, not the contract. The contract is
in 4b.

---

## 5. Backwards compatibility

### Adding `Create` to a `tag = "op"` enum is additive

The relevant attribute, quoted from
[`payloads.rs:265-266`](../../../../Deferno/backend/src/payloads.rs):

```rust
#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "lowercase")]
pub enum BatchOperation {
```

`tag = "op"` puts serde in **internally-tagged enum** mode:
each variant deserialises from a JSON object with a string
field `op` whose value matches the lowercased variant name.
The deserialiser routes by tag-equality, not by exhaustiveness.
Adding `Create` (tag value `"create"`) does not affect routing
of `"update"` or `"move"`.

The webui's `batch` method in
[`Deferno/webui/src/api/client.ts`](../../../../Deferno/webui/src/api/client.ts)
does not emit `"op": "create"`, so existing webui callers are
unaffected.

`BatchResponse { tasks: Vec<Task> }` ([`payloads.rs:285-288`](../../../../Deferno/backend/src/payloads.rs))
is unchanged. Existing callers iterating `tasks` keep seeing
updated/moved tasks. They additionally see created tasks if they
opt in by sending `"op": "create"`.

### The atomicity refactor changes observed behaviour

This is the only non-purely-additive change in the spec. Today's
`batch_operations` can leave a partial-flush mid-failure
(documented in Section 2). After this spec, it cannot — the
new model rolls back every staged orphan and applies no
mutations to existing tasks until the commit block.

This is a **strict improvement**: any caller that relied on
the documented contract ("if any operation fails nothing is
persisted") gets exactly that. Any caller that observed the
undocumented partial-flush behaviour was relying on a bug.
There is no breakage.

### No migration

No schema migration. No Redis-key shape change. No API-version
bump. Existing data is untouched. MCP clients on a release that
knows about `"op": "create"` start using it; older clients
continue unchanged.

---

## 6. Open questions

1. **Position argument on a top-level Create.** Defaults to
   "append at end of parent's children" / "append at end of
   root order". If callers want to insert-at-position in one
   call, that is an additive follow-up: add an optional
   `position` field on top-level `BatchCreateFields`. Not in v1.

2. **Refactoring the existing `apply_update` / `apply_move` to
   use the new stage/wire/commit model.** v1 keeps the existing
   mutation logic and only changes the *flush* to be atomic.
   A future refactor could fold all three op types into a
   single uniform tree-walk, but that is a larger code-quality
   pass, not a contract change.

3. **Partial-tree visibility on commit failure.** Today a
   commit failure (rare — Redis EXEC returning an error after
   queueing) triggers Phase 5 cleanup of staged orphans. The
   model assumes the EXEC error fires before any of the queued
   commands execute (per Redis semantics). If a future store
   has weaker guarantees (some queued commands ran, others
   didn't), the cleanup phase needs to also undo any partial
   commits. Out of scope for v1; revisit when the underlying
   store changes.
