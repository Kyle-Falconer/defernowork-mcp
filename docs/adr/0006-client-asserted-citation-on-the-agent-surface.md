# Client-asserted citation on the agent surface: a dedicated `cite_item`, citation as a Ref input form, and discovery off the tool budget

**Status:** accepted (extends [ADR-0001](0001-transparent-ref-resolution.md); amends the
tool count of [ADR-0005](0005-agent-relevant-surface-reduction.md) and the projection of
[ADR-0002](0002-compact-default-consolidated-read-surface.md); leaves
[ADR-0003](0003-behavioral-capture-caller-categorized-creation.md)'s capture contract
**untouched**, which is the point)

Deferno #622 opens [[External provenance]] to clients: an agent may assert that an item
corresponds to an upstream record (`jira:PROJ-123`) for a system Deferno has no
integration with. This ADR fixes how that reaches the agent — and records that the
answer **moved Deferno's own identity model**, because the constraint that decided it
lives here, not there.

Four moves:

- **A dedicated `cite_item` tool** (31 → 32), not a widening of `capture_item` and not
  five more parameters on `update_item`.
- **`RefForm.CITATION`** — a citation becomes a **Ref input form**, so every ref-taking
  tool resolves one transparently with no new parameter anywhere.
- **Discovery off the tool budget** — a `defernowork://sources` resource plus a
  rejection error that names near-matches; the source vocabulary never appears in a
  docstring.
- **`external` enters the single-item Compact projection only**, never list rows.

## Move 1 — `cite_item`, and why `capture_item` was never available

ADR-0003 rejected "extend `capture_item` with advanced fields," and ADR-0005 reaffirmed
it in the strongest available way: it **deleted `create_task` entirely** rather than fold
`parent_id` / `desire` onto `capture_item`, knowingly paying a two-call cost to protect
the frozen 1:1 contract with `deferno-kmp` (`tests/spec/capture/vectors.json`). ADR-0003
also states the governing line directly — *"**Source never votes on kind** … external
provenance is orthogonal to the kind decision and is a contained, projection-local field,
not a discriminator."*

So the citation arrives **one call after the row exists**. That is not a client
inconvenience; it is a fact with a consequence upstream, recorded below.

Given that, the write lands either on `update_item` or on a tool of its own.
`update_item` is the surface ADR-0005 measured at **1066 tokens** and named "the bloat
pattern" while rejecting an `operation`-enum comment tool. Folding a citation there
violates that ADR's own consolidation rule — *"consolidate by shared **shape** … not by
topical grouping"* — since citation parameters share no shape with a field patch. It
deepens the conditional-union prose that tool already carries ("Task-only:",
"Event-only:", "requires `recurring_scope`") with a fourth conditional keyed on the row's
provenance state, and it buries a conflict-returning, alias-reserving side effect inside a
tool that otherwise only patches fields.

`cite_item` passes ADR-0005's own cut criterion — *"does an autonomous agent drive this at
all?"* — more cleanly than almost anything on the surface: agent-driven citation is the
motivating scenario of #622. It is not sugar (nothing composes it) and not a
human/admin surface, so neither reduction move that ADR defines applies to it.

```
cite_item(ref, source, external_id, id_form, account?, url?)
```

- **`id_form` is required**, from `canonical | human_ref | url`. It is not derived:
  `canonical` vs `human_ref` is undecidable without the per-source grammar the Source
  registry deliberately refuses to declare. It is low-stakes to get wrong — it is not an
  input to the citation key, so a mislabel never fragments identity.
- **`composite` is server-reserved.** Deferno's glossary defines the form as a
  *Deferno-assembled* tuple. A client asserting one for an unmirrored source is inventing
  a convention that does not exist yet — three agents citing one Slack message would write
  `#general:1234.5678`, `C0123ABC:1234.5678`, and `T01/C0123ABC/p1234567800`, all honest,
  all hashing differently, and none rejectable, since the registry declines to declare
  grammar. `composite` is the one id form where client assertion actively reproduces the
  fragmentation the registry exists to prevent. A client holding a Slack message supplies
  the permalink as `url`, which is what Slack hands a human anyway.
- **Clearing is `source=None`**, not a fourth three-state convention bolted onto
  `update_item`.
- **No `on_conflict` parameter.** That is atomicity sugar the agent can compose, and
  ADR-0005's sugar-collapse posture rejects it.

### The conflict contract

A citation key already bound to another item returns Deferno's shipped **409
`item_exists`** shape, embedding the holder — one round trip instead of two, and a
precedent rather than a new shape. The message reads affirmatively (*"already captured as
`acme-412`"*), because agents abandon errors and use data, and here the response is
genuinely the answer.

The docstring **must not instruct a deletion**. The agent typically holds a
just-captured duplicate, and "delete your row" handed to an autonomous agent that
misidentifies which row is the duplicate destroys user data. The tool reports the
situation and lets the model reason.

Re-citing a row with the citation it already carries is an **idempotent success**, not a
conflict — a timed-out call retried is the most likely repeat this tool will ever see.

## Move 2 — a citation is a Ref input form

ADR-0001's classifier is the sanctioned extension point; `refs.py` says so outright
(*"Issue #9 extends this — **not** the callers"*). `RefForm.CITATION` recognises
`{registered_source}:{rest}`, which is unambiguous **precisely because the Source
registry is closed** — the form auto-routes only when the prefix is a registered key or
`x-`, exactly the "auto-route unambiguous forms, escape-hatch the rest" rule CONTEXT.md
already states for `ABC-223`.

This costs **zero tools and zero parameters** and every ref-taking tool inherits it
through `resolve_ref`: `get_item("jira:PROJ-123")`, `update_item(ref="jira:PROJ-123")`,
`post_item_comment(ref="jira:PROJ-123")`.

It is also what makes the surface usable rather than merely correct. Because citation is
a second call, the duplicate row exists *before* dedup can fire; an agent that resolves
the ticket first never creates it. That resolve is a pre-flight optimisation over a
correct backstop (the 409), not the dedup mechanism — two agents racing in the same
second still produce a duplicate plus a conflict. Sequential re-citation, which is the
real scenario (#622's own "Monday agent, Wednesday browser extension"), is fully handled.

**The MCP must not compute the citation key.** The hash is
`deterministic_uuid_for_external_ref` in the backend; resolution goes through a
server-side by-citation lookup. A Python copy of an identity function is precisely the
cross-language drift ADR-0003 calls "a correctness bug," and here drift would silently
split one record into two rows.

## Move 3 — discovery off the tool budget

The Source registry exists to stop four clients writing `jira`, `Jira`, `atlassian`,
`jira-cloud`. But it ships with an `x-` hatch that accepts any slug and never rejects, so
an agent whose guess is refused has two ways forward: find the right key, or retry
`x-{guess}`, which always succeeds. Agents optimise for the call succeeding. Unless
discovery is **cheaper than escaping**, the hatch wins by default — and because `x-` rows
never adopt, that outcome is permanent per row and silent.

- **`defernowork://sources`** — a resource, so it costs nothing in the tool schema
  every session pays for. Carries the dictionary with display metadata, canonical id form,
  and adoptable flag.
- **The rejection names near-matches** — *"`jira-cloud` is not a registered source; did
  you mean `jira`?"* The highest-leverage lever, because it fires exactly when the caller
  is wrong and converts a guess into a correction in one turn.
- **No enumeration in docstrings.** The dictionary is explicitly a follow-up in #622 and
  will grow; inlining it makes every agent pay for the whole vocabulary every session,
  which is the bloat ADR-0005 exists to fight. Docstrings name the registry, point at the
  resource, and state the `x-` rule *with its consequence*.

## Move 4 — `external` in the single-item Compact projection

`project()` is a flat top-level whitelist and `external` appears in neither
`COMPACT_ITEM_CORE_FIELDS` nor `COMPACT_ITEM_FIELDS`, so an agent sees no provenance at
all without `full=true`. Post-#622 that means it cannot tell a row is already cited, and
cannot see why a title edit returned 403.

`external` joins **`COMPACT_ITEM_FIELDS`** (single-item reads) and **not** the core list
rows. That matches the line the backend already draws — `origin_label` is detail-only for
the same reason — and single-item Compact already carries `description`, so an ~8-key
object is proportionate there in a way it is not across fifty list rows where it is null
on nearly all of them. The agent's dedup path is the `CITATION` resolve, not list
scanning.

A *reduced* `external` on list rows was considered and rejected: it needs nested
projection, a mechanism ADR-0002 deliberately does not have, and inventing it for one
field is not worth the precedent.

## Considered options

- **Widen `capture_item` with citation parameters.** The one-call path, and its usual
  objection is weaker than it looks: unlike `parent_id` / `desire`, a citation is
  **kind-neutral** — legal on all four derived kinds — so it does not reintroduce the
  mode-conditional-parameter defect that was ADR-0003's substantive complaint. And KMP
  *follows* the MCP per ADR-0003 and is not shipped, so this is a coordinated extension
  rather than a break of a live consumer. Rejected anyway: the golden vectors are the
  cross-repo contract, and once dedup no longer depends on create-time citation (see
  Consequences) the fold buys one round trip and nothing else — the same trade ADR-0005
  already made against, in the same direction, for the same tool.
- **A dedicated `capture_linked_item`.** Rejected: it duplicates the entire behavioral
  schema a second time (the most expensive option by token count), splits the
  "single create front door" invariant, and forces the agent to know a citation exists
  *before* capturing — which is exactly the ordering that does not hold in practice.
- **Fold onto `update_item`.** +0 tools, and rejected on ADR-0005's own consolidation
  rule; see Move 1.
- **A `list_sources` tool.** Rejected: +1 tool for data that is static, unbounded in
  growth, and read once — the textbook case for a resource, and this server already
  ships bounded resources under ADR-0002.
- **Compute the citation key client-side to skip a round trip.** Rejected: a
  cross-language copy of an identity function drifts, and drift here splits one upstream
  record into two Deferno rows — the exact failure #622 exists to prevent.
- **Rely on pre-flight resolution alone, with no conflict handling.** Rejected: a
  check-then-act race between two agents, and #622's user story 16 asks explicitly that
  idempotency not be the caller's job.
- **Require an acknowledgment flag to use `x-`.** Rejected: an agent willing to write
  `x-` is equally willing to set `never_adopt=true`, so it buys friction without intent.

## Consequences

- **The surface is 32 tools**, amending ADR-0005's 31. Still clear of Cursor's ~40 cap,
  but the headroom is now 8 and every future addition should be weighed against that
  ADR's cut criterion rather than against the cap.
- **ADR-0002's Compact projection gains a field** on the single-item view only; the list
  row set is unchanged.
- **Backend dependencies.** Three, none of which this repo can stub: a by-citation
  resolve endpoint (server-side hashing), the registry contents behind the `sources`
  resource, and the near-match suggestion in the rejection body. `cite_item` and
  `RefForm.CITATION` cannot ship before them.
- **This ADR moved Deferno's identity model.** #622 originally minted a linked row's
  Deferno id *from* the citation hash, which made deduplication a create-time property.
  That is unreachable from an agent surface whose create tool cannot carry a citation —
  and, more importantly, it is the wrong product behaviour regardless: a user who captures
  a task Monday and realises Tuesday it is `ENG-123` must not have to delete and
  re-capture, which destroys the subtasks, comments and plan membership that feature
  promises to preserve. Deferno consequently moved deduplication to an org-scoped
  **citation key** in its alias index, keyed on the same hash, with the row's own id left
  random. Recorded here because the constraint originated here, and a future reader of
  the Deferno ADR will otherwise not know why.
- **This server is the proof case for Deferno's presentation decision, and a deliberate
  partial exception to it.** Deferno settled that mirrored-vs-linked is server-determined
  and reaches the user as **text** in the origin label — never as a colour or icon —
  partly because this server has no visual channel at all. An agent therefore
  distinguishes a synced row from an unverified citation by reading a string, which is
  the cheapest available proof the distinction is genuinely in the text rather than in
  CSS. The exception: Deferno now emits that label on list projections too, and this
  server still drops `external` from list rows for the token reasons in Move 4. That is a
  knowing trade — an agent that needs provenance reads the item — but it means a *listing*
  agent cannot tell the two apart, and any future surface that asks an agent to reason
  over provenance across many rows at once must revisit it rather than assume coverage.
- **Nothing in this server may re-derive the distinction.** Not from the `source` string,
  not from `write_policy`, not from the presence of `external`. The value is rendered as
  received. This is the same rule the webui and KMP clients are held to, and it is worth
  a test here rather than a comment, because a plausible-looking local derivation is
  exactly the kind of helpfulness that would present a hallucinated citation as a synced
  fact.
- **Token impact is unmeasured.** ADR-0005 set the precedent of reporting real numbers
  from `scripts/measure_tool_context.py`; this ADR deliberately states none rather than
  estimate. Run it against the implemented surface and record the delta from the 9,396
  baseline before closing the work.
