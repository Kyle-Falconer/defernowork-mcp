"""Hand-curated registry of every backend endpoint, grouped by Rust handler module.

Discipline: when a route is added in `Deferno/backend/src/handlers/`, add or
remove the corresponding entry here in the same MCP-side PR. ``inventory.py``
cross-checks this list against the architecture doc and the on-disk fixture
tree; any mismatch fails the suite.

Each entry's ``operation`` field is the unique identifier shared with the
JSON fixture file (e.g. ``tasks.create`` corresponds to
``tests/spec/v0.1/tasks/create.json``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    handler: str       # Rust handler module: "auth", "tasks", "items", "admin", "internal",
                       # "health", "habits", "chores", "events", "comments",
                       # "saved_searches", "feedback"
    method: str
    path: str          # path template as written in architecture.md
    operation: str     # matches the fixture's "operation" field
    auth: str          # "none" | "bearer" | "bearer-admin" | "internal-shared-secret"


ENDPOINTS: list[Endpoint] = [
    # ── handlers::health (public, outside /api) ─────────────────────────
    Endpoint("health", "GET", "/health", "health.get", "none"),

    # ── handlers::auth (public) ─────────────────────────────────────────
    Endpoint("auth", "GET",  "/auth/oidc/login",        "auth.oidc_login",       "none"),
    Endpoint("auth", "GET",  "/auth/oidc/register",     "auth.oidc_register",    "none"),
    Endpoint("auth", "GET",  "/auth/oidc/callback",     "auth.oidc_callback",    "none"),
    Endpoint("auth", "POST", "/auth/logout",            "auth.logout",           "bearer"),

    # ── handlers::auth (authenticated) ──────────────────────────────────
    Endpoint("auth", "GET",    "/auth/me",              "auth.me_get",           "bearer"),
    Endpoint("auth", "PATCH",  "/auth/me",              "auth.me_patch",         "bearer"),
    Endpoint("auth", "GET",    "/auth/me/settings",     "auth.settings_get",     "bearer"),
    Endpoint("auth", "PATCH",  "/auth/me/settings",     "auth.settings_patch",   "bearer"),
    Endpoint("auth", "GET",    "/auth/tokens",          "auth.tokens_list",      "bearer"),
    Endpoint("auth", "POST",   "/auth/tokens",          "auth.tokens_create",    "bearer"),
    Endpoint("auth", "DELETE", "/auth/tokens/{id}",     "auth.tokens_delete",    "bearer"),
    Endpoint("auth", "PATCH",  "/auth/tokens/{id}",     "auth.tokens_rename",    "bearer"),
    Endpoint("auth", "GET",    "/auth/connected-mcp",   "auth.connected_mcp",    "bearer"),

    # ── handlers::admin ─────────────────────────────────────────────────
    Endpoint("admin", "GET", "/admin/users", "admin.users_list", "bearer-admin"),
    Endpoint("admin", "GET", "/admin/stats", "admin.stats",      "bearer-admin"),

    # ── internal (nginx-blocked) ────────────────────────────────────────
    Endpoint("internal", "POST", "/internal/mcp-session", "internal.mcp_session", "internal-shared-secret"),

    # ── handlers::items (cross-kind) ────────────────────────────────────
    Endpoint("items", "GET",    "/items",                  "items.list",            "bearer"),
    Endpoint("items", "GET",    "/items/{id}",             "items.get",             "bearer"),
    Endpoint("items", "DELETE", "/items/{id}",             "items.delete",          "bearer"),
    Endpoint("items", "GET",    "/items/{id}/history",     "items.history",         "bearer"),
    Endpoint("items", "GET",    "/items/{id}/comments",    "items.comments_list",   "bearer"),
    Endpoint("items", "POST",   "/items/{id}/comments",    "items.comments_create", "bearer"),
    Endpoint("items", "POST",   "/items/{id}/split",       "items.split",           "bearer"),
    Endpoint("items", "POST",   "/items/{id}/merge",       "items.merge",           "bearer"),
    Endpoint("items", "POST",   "/items/{id}/move",        "items.move",            "bearer"),
    Endpoint("items", "POST",   "/items/{id}/pin",         "items.pin",             "bearer"),
    Endpoint("items", "POST",   "/items/{id}/convert",     "items.convert",         "bearer"),
    Endpoint("items", "GET",    "/items/calendar",         "items.calendar",        "bearer"),
    Endpoint("items", "GET",    "/items/plan",             "items.plan_get",        "bearer"),
    Endpoint("items", "POST",   "/items/plan/add",         "items.plan_add",        "bearer"),
    Endpoint("items", "POST",   "/items/plan/remove",      "items.plan_remove",     "bearer"),
    Endpoint("items", "POST",   "/items/plan/reorder",     "items.plan_reorder",    "bearer"),

    # ── handlers::tasks ─────────────────────────────────────────────────
    Endpoint("tasks", "GET",    "/tasks",                       "tasks.list",            "bearer"),
    Endpoint("tasks", "POST",   "/tasks",                       "tasks.create",          "bearer"),
    Endpoint("tasks", "GET",    "/tasks/today",                 "tasks.today",           "bearer"),
    Endpoint("tasks", "GET",    "/tasks/plan",                  "tasks.plan_get",        "bearer"),
    Endpoint("tasks", "POST",   "/tasks/plan/add",              "tasks.plan_add",        "bearer"),
    Endpoint("tasks", "POST",   "/tasks/plan/remove",           "tasks.plan_remove",     "bearer"),
    Endpoint("tasks", "POST",   "/tasks/plan/reorder",          "tasks.plan_reorder",    "bearer"),
    Endpoint("tasks", "GET",    "/tasks/calendar",              "tasks.calendar",        "bearer"),
    Endpoint("tasks", "GET",    "/tasks/export",                "tasks.export",          "bearer"),
    Endpoint("tasks", "POST",   "/tasks/import",                "tasks.import",          "bearer"),
    Endpoint("tasks", "POST",   "/tasks/batch",                 "tasks.batch",           "bearer"),
    Endpoint("tasks", "GET",    "/tasks/search",                "tasks.search",          "bearer"),
    Endpoint("tasks", "DELETE", "/tasks/all",                   "tasks.delete_all",      "bearer"),
    Endpoint("tasks", "GET",    "/tasks/mood-history",          "tasks.mood_history",    "bearer"),
    Endpoint("tasks", "GET",    "/tasks/{id}",                  "tasks.get",             "bearer"),
    Endpoint("tasks", "PATCH",  "/tasks/{id}",                  "tasks.patch",           "bearer"),
    Endpoint("tasks", "DELETE", "/tasks/{id}",                  "tasks.delete",          "bearer"),
    Endpoint("tasks", "POST",   "/tasks/{id}/split",            "tasks.split",           "bearer"),
    Endpoint("tasks", "POST",   "/tasks/{id}/merge",            "tasks.merge",           "bearer"),
    Endpoint("tasks", "POST",   "/tasks/{id}/fold",             "tasks.fold",            "bearer"),
    Endpoint("tasks", "POST",   "/tasks/{id}/move",             "tasks.move",            "bearer"),
    Endpoint("tasks", "GET",    "/tasks/{task_id}/comments",    "tasks.comments_list",   "bearer"),
    Endpoint("tasks", "POST",   "/tasks/{task_id}/comments",    "tasks.comments_create", "bearer"),
    Endpoint("tasks", "GET",    "/tasks/pinned",                "tasks.pinned_get",      "bearer"),
    Endpoint("tasks", "POST",   "/tasks/pinned/reorder",        "tasks.pinned_reorder",  "bearer"),
    Endpoint("tasks", "PATCH",  "/tasks/pinned/{id}",           "tasks.pinned_label",    "bearer"),

    # ── handlers::habits ────────────────────────────────────────────────
    Endpoint("habits", "POST",   "/habits",                              "habits.create",            "bearer"),
    Endpoint("habits", "PATCH",  "/habits/{id}",                         "habits.patch",             "bearer"),
    Endpoint("habits", "DELETE", "/habits/{id}",                         "habits.delete",            "bearer"),
    Endpoint("habits", "GET",    "/habits/{id}/occurrences",             "habits.occurrences_list",  "bearer"),
    Endpoint("habits", "POST",   "/habits/{id}/occurrences",             "habits.occurrences_mark",  "bearer"),
    Endpoint("habits", "DELETE", "/habits/{id}/occurrences/{date}",      "habits.occurrences_clear", "bearer"),

    # ── handlers::chores ────────────────────────────────────────────────
    Endpoint("chores", "POST",   "/chores",                              "chores.create",                 "bearer"),
    Endpoint("chores", "PATCH",  "/chores/{id}",                         "chores.patch",                  "bearer"),
    Endpoint("chores", "DELETE", "/chores/{id}",                         "chores.delete",                 "bearer"),
    Endpoint("chores", "GET",    "/chores/{id}/occurrences",             "chores.occurrences_list",       "bearer"),
    Endpoint("chores", "PUT",    "/chores/{id}/occurrences/{date}",      "chores.occurrences_set_status", "bearer"),
    Endpoint("chores", "POST",   "/chores/{id}/mark-next-done",          "chores.mark_next_done",         "bearer"),

    # ── handlers::events ────────────────────────────────────────────────
    Endpoint("events", "POST",   "/events",        "events.create", "bearer"),
    Endpoint("events", "PATCH",  "/events/{id}",   "events.patch",  "bearer"),
    Endpoint("events", "DELETE", "/events/{id}",   "events.delete", "bearer"),

    # ── handlers::comments (creator-only) ───────────────────────────────
    Endpoint("comments", "PATCH",  "/comments/{comment_id}", "comments.patch",  "bearer"),
    Endpoint("comments", "DELETE", "/comments/{comment_id}", "comments.delete", "bearer"),

    # ── handlers::saved_searches ────────────────────────────────────────
    Endpoint("saved_searches", "GET",    "/saved-searches",          "saved_searches.list",     "bearer"),
    Endpoint("saved_searches", "POST",   "/saved-searches",          "saved_searches.create",   "bearer"),
    Endpoint("saved_searches", "PATCH",  "/saved-searches/{id}",     "saved_searches.patch",    "bearer"),
    Endpoint("saved_searches", "DELETE", "/saved-searches/{id}",     "saved_searches.delete",   "bearer"),
    Endpoint("saved_searches", "POST",   "/saved-searches/reorder",  "saved_searches.reorder",  "bearer"),

    # ── handlers::feedback ──────────────────────────────────────────────
    Endpoint("feedback", "POST",  "/feedback",                                                "feedback.create",         "bearer"),
    Endpoint("feedback", "GET",   "/feedback",                                                "feedback.list",           "bearer-admin"),
    Endpoint("feedback", "GET",   "/feedback/stats",                                          "feedback.stats",          "bearer-admin"),
    Endpoint("feedback", "PATCH", "/feedback/{id}",                                           "feedback.patch",          "bearer-admin"),
    Endpoint("feedback", "GET",   "/feedback/{feedback_id}/attachments/{attachment_id}",      "feedback.attachment_get", "bearer-admin"),
]
