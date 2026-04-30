<!-- ───────────────────────────── WIP BANNER ───────────────────────────── -->
> # ⚠️⚠️⚠️ WIP — DO NOT IMPLEMENT ⚠️⚠️⚠️
>
> **Status:** work-in-progress. Has unresolved design issues, including
> at least two blocker-level findings (atomicity guarantee does not
> hold; orphan reconciler can destroy live data under concurrent load).
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

# `/tasks/batch` — `Create` variant + nested wire format — design

**Status:** draft
**Date:** 2026-04-29
**Repos:** `Deferno` (Rust backend), `defernowork-mcp` (Python MCP server)
**Source of truth:** the Rust types in [`Deferno/backend/src/payloads.rs`](../../../../Deferno/backend/src/payloads.rs) and the handler in [`Deferno/backend/src/handlers/tasks.rs`](../../../../Deferno/backend/src/handlers/tasks.rs). The MCP `batch` tool docstring is descriptive, not authoritative.

**Related specs (sibling — same date):**
- [`2026-04-29-batch-atomicity-refactor-design.md`](2026-04-29-batch-atomicity-refactor-design.md) — defines the four-phase atomicity model and staging-key contract used here. **PREREQUISITE — must land first.**
- [`2026-04-29-orphan-reconciler-design.md`](2026-04-29-orphan-reconciler-design.md) — the cleanup guarantee for staging keys whose explicit DEL fails. **PREREQUISITE — must land first.**

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
3. Plug into the four-phase model defined by the sibling
   atomicity-refactor spec so the whole batch — creates included —
   either commits or rolls back as a single atomic unit.

The wire format and the atomicity model are independent decisions;
the atomicity guarantee comes from the sibling spec, this spec
adds the wire format and the create-staging logic that consumes
it.

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

Add `BatchOperation::Create` with a wire format that expresses a
tree, and wire its create logic into the staging-key + atomic-
commit + reconciler-backed-cleanup model defined by the sibling
specs so the whole batch (creates, updates, moves) commits or
rolls back atomically.

### Success criteria

1. `BatchOperation::Create { fields: BatchCreateFields }`
   deserialises from a JSON object tagged `"op": "create"`.
2. `BatchCreateFields` carries the same fields as `CreateTaskPayload`
   plus a recursive `children: Vec<BatchCreateFields>` array.
   `parent_id` is allowed only at the top level; on a nested
   create, the parent is implicit.
3. The handler walks the create-tree depth-first, mints UUIDs,
   wires `parent_id` / `children` in memory, and stages each new
   task blob to `staged:task:{uid}:{newId}` (the contract from
   the atomicity-refactor spec) — orphans, with TTL set, no
   membership in the user's task set.
4. After validation passes for every op (creates, updates,
   moves), the handler issues a single `MULTI`/`EXEC` block that
   RENAMEs every staging key into its final `task:{newId}` slot,
   PERSISTs to remove the staging TTL, SADDs the new ids into
   `user:{uid}:tasks` and `user:{uid}:item_kinds`, applies all
   in-memory mutations to pre-existing tasks, and updates the
   search index. There is no observable middle state.
5. If validation fails at any step, every staging key from this
   batch is DEL'd synchronously. If any DEL fails, the orphan
   reconciler (sibling spec) drains it on its next sweep. No
   mutation to pre-existing tasks has been written at this
   point (mutations are held in-memory until commit).
6. The MCP `batch` tool's docstring documents the nested format,
   the implicit-parent rule for nested creates, and the atomic
   guarantee.
7. RED tests cover the nested create round-trip, the
   atomic-rollback path (a failure mid-batch leaves no trace —
   verified against staging-key absence and final-keyspace
   absence), and the implicit-parent wiring.

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
- **The atomicity model itself.** That is the sibling spec
  [`2026-04-29-batch-atomicity-refactor-design.md`](2026-04-29-batch-atomicity-refactor-design.md);
  this spec consumes its model.
- **Staging-key cleanup guarantees.** That is the sibling spec
  [`2026-04-29-orphan-reconciler-design.md`](2026-04-29-orphan-reconciler-design.md);
  this spec relies on its guarantees.

---

## Architecture

### File touch list

```
Deferno/backend/src/
  payloads.rs                   # add BatchCreateFields (recursive) + BatchOperation::Create variant
  handlers/tasks.rs             # batch_tasks gains a Create match arm + tree-walk pre-validation
  models.rs                     # BatchOp::Create variant on the repository-level enum
  repository/batch.rs           # plug stage_create_tree into Phase 1 of the four-phase model
  repository/tasks.rs           # add stage_create_tree helper (SET to staged:task:{uid}:{newId} with TTL)

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

## 2. Plugging into the four-phase atomicity model

The sibling spec
[`2026-04-29-batch-atomicity-refactor-design.md`](2026-04-29-batch-atomicity-refactor-design.md)
defines the model: stage / wire / commit / cleanup. This spec
fills in Phase 1 (stage) and Phase 5 (cleanup) for `Create` ops,
and extends Phase 4 (commit) to atomically promote staged blobs
into final keys.

### Phase 1 — stage the create-tree

For every top-level `BatchOperation::Create`, recursively walk
the tree depth-first, pre-order:

1. Mint `new_id = Uuid::new_v4()`.
2. Build `Task::new_with_id(new_id, fields)` setting `parent_id`
   from `fields.parent_id` (top-level only) and `created_by =
   Some(user_id)`.
3. For each child in `fields.children`:
   - Mint `child_id`.
   - Build child Task with `parent_id = Some(new_id)`,
     `created_by = Some(user_id)`.
   - Recurse into grandchildren.
   - Append `child_id` to the parent Task's `children` array.
4. Encrypt and `SET staged:task:{uid}:{new_id} <encrypted_blob>
   EX <STAGE_TTL_SECS>` — staging write with TTL. The TTL is
   the defense-in-depth backstop; the orphan reconciler is the
   actual cleanup guarantee.
5. Push `(uid, new_id)` onto `staged_orphans` so Phase 5 (and
   Phase 4 RENAME) can find them.

Order matters: the child task blobs are wired into the parent's
`children` array purely in memory before any blob is encrypted,
so when blobs are written the in-memory tree is fully formed.
The actual write order across staging keys does not matter
because no read path observes them until Phase 4's atomic
RENAME.

```rust
// Deferno/backend/src/repository/batch.rs (Phase 1 helper)

async fn stage_create_tree(
    &self,
    conn: &mut redis::aio::Connection,
    ops: &[BatchOp],
    user_id: Uuid,
    dek: &SecretKey,
    staged_orphans: &mut Vec<(Uuid, Uuid)>,
) -> Result<StagedCreates> {
    let stage_ttl_secs: u64 = std::env::var("STAGE_TTL_SECS")
        .ok().and_then(|s| s.parse().ok()).unwrap_or(3600);

    let mut tasks: Vec<Task> = Vec::new();
    for op in ops {
        if let BatchOp::Create { fields } = op {
            let root_id = self.stage_one_create_node(
                conn, fields, /* parent_id_override */ None,
                user_id, dek, &mut tasks, staged_orphans,
                stage_ttl_secs,
            ).await?;
            // root_id is included in `tasks` already.
            let _ = root_id;
        }
    }
    Ok(StagedCreates { tasks })
}

#[allow(clippy::too_many_arguments)]
fn stage_one_create_node<'a>(
    &'a self,
    conn: &'a mut redis::aio::Connection,
    fields: &'a BatchCreateFields,
    parent_id_override: Option<Uuid>,
    user_id: Uuid,
    dek: &'a SecretKey,
    out_tasks: &'a mut Vec<Task>,
    staged_orphans: &'a mut Vec<(Uuid, Uuid)>,
    stage_ttl_secs: u64,
) -> futures::future::BoxFuture<'a, Result<Uuid>> {
    Box::pin(async move {
        let new_id = Uuid::new_v4();
        // parent_id_override is Some(parent_new_id) for nested
        // children; None for top-level (the fields' own
        // parent_id, if any, is taken instead).
        let parent_id = parent_id_override.or(fields.parent_id);
        let mut task = Task::new_with_id(new_id, user_id, fields, parent_id);

        // Recurse into children, collecting their ids into our
        // children array.
        for child_fields in &fields.children {
            let child_id = self.stage_one_create_node(
                conn, child_fields, Some(new_id), user_id, dek,
                out_tasks, staged_orphans, stage_ttl_secs,
            ).await?;
            task.children.push(child_id);
        }

        // Encrypt and SET to staging key with TTL.
        let blob = encrypt_task(&task, dek)?;
        redis::cmd("SET")
            .arg(Self::staged_task_key(user_id, new_id))
            .arg(blob)
            .arg("EX").arg(stage_ttl_secs)
            .query_async::<()>(conn).await?;
        staged_orphans.push((user_id, new_id));
        out_tasks.push(task);

        Ok(new_id)
    })
}
```

### Phase 4 — extend commit to RENAME staged keys

Inside the existing `pipe.atomic()` block (defined by the
atomicity-refactor spec), append the visibility-flip commands
for each staged task:

```rust
fn queue_visibility_flip(
    pipe: &mut redis::Pipeline,
    user_id: Uuid,
    new_task: &Task,
) {
    let staged_key = format!("staged:task:{}:{}", user_id, new_task.id);
    let final_key = format!("task:{}", new_task.id);

    // RENAME inherits the source's TTL. PERSIST clears it on the
    // destination so the committed blob doesn't expire.
    pipe.cmd("RENAME").arg(&staged_key).arg(&final_key);
    pipe.cmd("PERSIST").arg(&final_key);
    pipe.cmd("SADD").arg(format!("user:{}:tasks", user_id))
        .arg(new_task.id.to_string());
    pipe.cmd("HSET").arg(format!("user:{}:item_kinds", user_id))
        .arg(new_task.id.to_string()).arg("task");

    // For root-level new tasks (parent_id == None), insert into
    // root_order. Children of new top-level creates are NOT in
    // root_order — they're reachable via their parent's
    // children array.
    if new_task.parent_id.is_none() {
        pipe.cmd("RPUSH").arg(format!("user:{}:root_order", user_id))
            .arg(new_task.id.to_string());
    }
}
```

The atomicity refactor spec's pseudocode already calls
`queue_visibility_flip`; this spec is what implements the
function body.

### Phase 5 — cleanup-on-failure

The atomicity refactor spec defines synchronous DEL of
`staged:task:{uid}:{newId}` for every entry in
`staged_orphans`. This spec confirms the contract:

- Synchronous DEL is the **fast path**. Most failures are
  recovered here.
- The reconciler (sibling spec) is the **guarantee**. If any
  synchronous DEL fails, the staging key persists. The
  reconciler's staging sweep (`SCAN MATCH staged:task:*`,
  age threshold) DELs it on the next pass, well before TTL
  fires.
- TTL is the **last-resort backstop**. If both the synchronous
  DEL and the reconciler somehow fail, Redis itself drops the
  key after `STAGE_TTL_SECS`.

No data loss: the staged key is invisible to all read paths
(no membership, no root_order, no item_kinds, no search).
No cross-user contamination: the staged key includes the uid;
DEL is per-key.

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
    // Reject parent_id appearing on nested children (recursive walk):
    walk_reject_nested_parent_id(&fields, idx)?;
    ops.push(BatchOp::Create { fields });
}
```

`walk_reject_nested_parent_id` is a recursive helper that walks
`fields.children` and returns
`Err(ApiError::new(BAD_REQUEST, "operation {idx}: parent_id is only valid on a top-level create"))`
if any nested entry has `parent_id == Some(_)`.

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
   `parent_id` is `None`. Also assert the staging key
   `staged:task:{uid}:{new.id}` does NOT exist post-commit
   (it was RENAMEd into `task:{new.id}`).

2. **`batch_create_persists_subtree_in_one_call`**
   *(The tree test the user explicitly asked for in the
   original spec.)*
   One-op batch with a parent and two children, one of which has
   a grandchild (4 new tasks total). Assert:
   - Response contains all 4 tasks.
   - The parent's `children` array has 2 entries; one of those
     children has 1 grandchild in its `children` array.
   - Each child's `parent_id` points at its parent's id.
   - `get_all_tasks` lists all 4.
   - No staging keys remain.

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
   exists for either user AND no staging key
   `staged:task:{B.uid}:*` remains.

5. **`batch_create_rejects_parent_id_on_nested`**
   Send `op=create` with a child that carries `parent_id`. Assert
   400 with message *"operation 0: parent_id is only valid on
   a top-level create"*. AND `get_all_tasks` is unchanged. AND
   no staging key remains (the rejection happens in the handler
   before Phase 1 stages anything).

6. **`batch_create_rejects_recurrence_at_every_depth`**
   Three sub-tests:
   - `recurrence` on the top-level create → 422.
   - `recurrence` on a child → 422.
   - `recurrence` on a grandchild → 422.
   In all three, no new task persists and no staging key
   remains.

7. **`batch_validation_failure_rolls_back_staged_orphans`**
   *(The atomicity test the user explicitly asked for.)*
   Three-op batch: op #0 creates task A, op #1 creates a subtree
   B + C + D, op #2 attempts to update a non-existent task id.
   Assert:
   - The call returns `404 NOT FOUND`.
   - `get_all_tasks` does NOT contain A, B, C, or D.
   - **Zero `task:*` keys exist for any of the four would-be
     IDs.**
   - **Zero `staged:task:{uid}:*` keys exist** — synchronous
     DEL ran successfully in Phase 5.

7a. **`batch_failure_with_redis_blip_leaves_staging_for_reconciler`**
   *(Proves the reconciler-as-guarantee path works.)*
   Inject a fault that makes the synchronous Phase 5 DEL fail
   for one specific staged orphan id (e.g. wrap the connection
   in a test-only adapter that returns an error on the first
   DEL of a configured id, then succeeds). Run a batch that
   fails validation. Assert:
   - The call returns the validation error to the caller.
   - One stale `staged:task:{uid}:{id}` key still exists in
     Redis (the synchronous DEL failed; nothing else has
     touched it yet).
   - It still has a TTL (PERSIST never ran).
   - Then call `repository.reconcile_orphan_tasks().await`
     with the test-only `STAGE_RECONCILE_AGE_SECS=0` override.
   - Assert: the staging key is gone AND
     `ReconcileReport.staged_swept >= 1`.

8. **`batch_create_update_move_in_one_call_succeeds`**
   *(The mixed-op test the user explicitly asked for.)*
   Pre-create tasks X and Y. Run batch:
   - op #0: create new tree N1 → [N2 → N3]
   - op #1: update X's status to `in-progress`
   - op #2: move Y to be a child of X
   Assert: response contains N1, N2, N3, X, Y; X's status is
   `in-progress`; X's `children` contains Y.id and N1.id is NOT
   in X's children (N1 is root); N1.children == [N2.id]; etc.
   No staging keys remain.

9. **`batch_concurrent_create_does_not_leak_orphans`**
   Spawn two `tokio::spawn`'d batch_operations calls that both
   create a 3-task subtree for the same user. Await both. Assert
   `get_user_tasks(user_id)` contains all 6 new tasks. Assert
   no `staged:task:{uid}:*` keys remain post both completions.

**Search-index assertions are out of scope for these tests** —
search index correctness is covered by
`repository/search.rs`'s own test suite. The atomicity tests
above only verify the user-set membership / parent-children
wiring / staged-key absence, which is what the atomicity
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

### No migration

No schema migration. No Redis-key shape change for existing
keys (the new `staged:task:*` keyspace is introduced by the
sibling atomicity-refactor spec). No API-version bump.

---

## 6. Open questions

1. **Position argument on a top-level Create.** Defaults to
   "append at end of parent's children" / "append at end of
   root order". If callers want to insert-at-position in one
   call, that is an additive follow-up: add an optional
   `position` field on top-level `BatchCreateFields`. Not in v1.

2. **Refactoring the existing `apply_update` / `apply_move` to
   use the new stage/wire/commit model.** v1 keeps the existing
   mutation logic and only changes the *flush* to be atomic
   (per the sibling atomicity-refactor spec). A future refactor
   could fold all three op types into a single uniform tree-walk
   over a unified `BatchOp` representation, but that is a larger
   code-quality pass, not a contract change.

3. **Partial-tree visibility on commit failure.** Today a
   commit failure (rare — Redis EXEC returning an error after
   queueing) triggers Phase 5 cleanup of staged orphans. The
   model assumes the EXEC error fires before any of the queued
   commands execute (per Redis semantics). If a future store
   has weaker guarantees (some queued commands ran, others
   didn't), the cleanup phase needs to also undo any partial
   commits. Out of scope for v1; revisit when the underlying
   store changes.
