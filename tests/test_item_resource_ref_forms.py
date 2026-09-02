"""Characterization of ``defernowork://item/{ref}`` across every Ref input form (#31).

The MCP SDK 1.x matches a resource URI template with a hand-rolled regex. It
rewrites ``{ref}`` to ``(?P<ref>[^/]+)`` and anchors it. SDK 2.x replaces that
with RFC 6570 expansion, which is stricter about path safety, literal matching
and empty values. These tests pin what 1.x actually does today, so the upgrade
has a baseline to diff against.

The two forms at risk carry path separators inside the template variable:

- the App URL ``https://app.defernowork.com/o/{org_slug}/items/{seq-or-id}``
- the GitHub Alias ``owner/repo#N``

Both must be percent-encoded before they reach the resource URI. Raw, they do
not match the 1.x template at all. See the module's matching section for the
pinned behavior.

Resolution of the plain UUID, Sequence shorthand and Canonical ref forms lives
in ``test_item_resource.py``. This file adds the forms that file lacks (App URL
and Alias), the routing exclusivity that proves a Sequence shorthand stays on
the personal-org endpoint, and the error behavior for malformed and empty refs.
"""

from __future__ import annotations

import json
from urllib.parse import quote

import httpx
import pytest
import respx

from defernowork_mcp import server as srv
from mcp.server.mcpserver import Context

from defernowork_mcp.client import DefernoClient, DefernoError

BASE = "http://test:3000/api"

# The caller's personal org. A Sequence shorthand resolves against this org and
# no other, because the backend by-seq route is personal-org only.
PERSONAL_SLUG = "u-1y0e2v"
PERSONAL_UUID = "11111111-2222-3333-4444-555555555555"

# A second, non-personal org the caller is a member of. Items here are
# reachable by Canonical ref or App URL, never by Sequence shorthand.
SHARED_SLUG = "acme"
SHARED_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

APP_URL_SEQ = f"https://app.defernowork.com/o/{SHARED_SLUG}/items/456"
APP_URL_UUID = f"https://app.defernowork.com/o/{SHARED_SLUG}/items/{SHARED_UUID}"
ALIAS = "octo/repo#42"

PERSONAL_ITEM = {
    "kind": "task",
    "type": "task",
    "id": PERSONAL_UUID,
    "ref": f"{PERSONAL_SLUG}-123",
    "org_slug": PERSONAL_SLUG,
    "sequence": 123,
    "title": "Personal task",
    "status": "active",
    "description": "the body",
    "comments": [{"body": "heavy"}],
}

SHARED_ITEM = {
    "kind": "task",
    "type": "task",
    "id": SHARED_UUID,
    "ref": f"{SHARED_SLUG}-456",
    "org_slug": SHARED_SLUG,
    "sequence": 456,
    "title": "Shared-org task",
    "status": "active",
    "description": "the body",
    "comments": [{"body": "heavy"}],
}


def _env(data):
    return {"version": "0.2", "data": data, "error": None}


@pytest.fixture
def server(monkeypatch):
    async def _stub_get_client_async(ctx=None):
        return DefernoClient(base_url=BASE, token="test-token")

    monkeypatch.setattr(srv, "_get_client_async", _stub_get_client_async)
    monkeypatch.setattr(srv, "_http_transport_mode", False)
    return srv.create_server()


def _template(mcp):
    return mcp._resource_manager._templates["defernowork://item/{ref}"]


async def _read(mcp, uri: str):
    """Resolve a resource URI through the registered surface and read it.

    Routes through ``ResourceManager.get_resource``, so the real template
    matcher decides whether the URI reaches the handler at all. That matcher is
    the machinery SDK 2.x replaces.
    """
    context = Context(mcp_server=mcp, subscriptions=mcp._subscriptions)
    resource = await mcp._resource_manager.get_resource(uri, context)
    body = await resource.read()
    return json.loads(body)


def _routes():
    """Register every item read route the resolver can reach.

    Returns the four respx routes so a test can assert which one ran and, just
    as importantly, which ones did not.
    """
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env({"id": PERSONAL_UUID, "kind": "task"}))
    )
    by_ref = respx.get(f"{BASE}/items/by-ref/{SHARED_SLUG}-456").mock(
        return_value=httpx.Response(200, json=_env({"id": SHARED_UUID, "kind": "task"}))
    )
    by_alias = respx.get(f"{BASE}/items/by-alias/{quote(ALIAS, safe='')}").mock(
        return_value=httpx.Response(200, json=_env({"id": SHARED_UUID, "kind": "task"}))
    )
    respx.get(f"{BASE}/items/{PERSONAL_UUID}").mock(
        return_value=httpx.Response(200, json=_env(PERSONAL_ITEM))
    )
    by_id_shared = respx.get(f"{BASE}/items/{SHARED_UUID}").mock(
        return_value=httpx.Response(200, json=_env(SHARED_ITEM))
    )
    return by_seq, by_ref, by_alias, by_id_shared



# ── what SDK 2.x changed here ───────────────────────────────────────────────
#
# This file pins the 1.x template matcher. The port to 2.x moved it, so the
# assertions below now describe behavior the server no longer has. Issue #35
# decides what the new behavior should be and makes these pass.
#
# Each marker names one change. A marker that starts passing means #35 landed
# and the marker comes off.

RFC_6570_DECODES = pytest.mark.xfail(
    strict=True,
    reason=(
        "2.x matches per RFC 6570 and percent-decodes the captured variable. "
        "1.x captured the raw text with [^/]+. See #35."
    ),
)

TYPED_RESOURCE_ERRORS = pytest.mark.xfail(
    strict=True,
    reason=(
        "2.x raises ResourceNotFoundError / UnexpectedResourceError where 1.x "
        "raised a doubly-wrapped ValueError. See #35."
    ),
)


# ── what the 1.x template matcher accepts ────────────────────────────────────
#
# 1.x builds the pattern by string replacement: ``{ref}`` becomes
# ``(?P<ref>[^/]+)``. Three consequences fall out of that one character class,
# and all three are what RFC 6570 matching in SDK 2.x will redefine.


@pytest.mark.parametrize(
    ("label", "ref"),
    [
        ("uuid", PERSONAL_UUID),
        pytest.param(
            "sequence shorthand, encoded",
            quote("#123", safe=""),
            marks=RFC_6570_DECODES,
        ),
        ("sequence shorthand, bare digits", "123"),
        ("canonical ref", f"{PERSONAL_SLUG}-123"),
        pytest.param(
            "app url, encoded",
            quote(APP_URL_SEQ, safe=""),
            marks=RFC_6570_DECODES,
        ),
        pytest.param(
            "app url with uuid tail, encoded",
            quote(APP_URL_UUID, safe=""),
            marks=RFC_6570_DECODES,
        ),
        pytest.param(
            "alias, encoded",
            quote(ALIAS, safe=""),
            marks=RFC_6570_DECODES,
        ),
    ],
)
def test_template_matches_every_ref_form_once_encoded(server, label, ref):
    """Every Ref input form matches the template when it carries no ``/``."""
    assert _template(server).matches(f"defernowork://item/{ref}") == {"ref": ref}


@pytest.mark.parametrize(
    ("label", "ref"),
    [
        ("raw app url", APP_URL_SEQ),
        ("raw alias", ALIAS),
    ],
)
def test_template_rejects_raw_forms_that_carry_path_separators(server, label, ref):
    """A raw App URL or Alias does not match: ``[^/]+`` stops at the first ``/``.

    This is the finding the issue was filed for. Percent-encoding is not a
    convenience here, it is the contract. A client that pastes an App URL
    verbatim into the resource URI gets no match and never reaches the handler.
    """
    assert _template(server).matches(f"defernowork://item/{ref}") is None


@RFC_6570_DECODES
def test_template_rejects_an_empty_ref(server):
    """``[^/]+`` requires at least one character, so an empty ref never matches.

    RFC 6570 defines empty-value expansion, so SDK 2.x may start matching this
    URI and handing the handler an empty string.
    """
    assert _template(server).matches("defernowork://item/") is None


@RFC_6570_DECODES
def test_template_matches_a_bare_hash_and_a_lone_space(server):
    """1.x matches on the raw string, so it accepts characters a URI should not carry.

    ``[^/]+`` excludes exactly one character. A literal ``#`` (which a real URI
    parser would read as a fragment delimiter) and a literal space both match.
    Path-safety checks in SDK 2.x are expected to reject both.
    """
    tpl = _template(server)
    assert tpl.matches("defernowork://item/#123") == {"ref": "#123"}
    assert tpl.matches("defernowork://item/ ") == {"ref": " "}


# ── App URL: template matching plus cross-org resolution ─────────────────────


@respx.mock
async def test_app_url_resolves_a_non_personal_org_item_by_ref(server):
    """A percent-encoded App URL for a shared org routes through by-ref.

    The handler calls ``unquote`` on the matched variable, so the scheme and
    separators come back before the classifier sees them. The classifier reads
    ``/o/{slug}/items/{seq}``, synthesizes the Canonical ref ``acme-456``, and
    routes by-ref. It must never route by-seq: the org in the URL is not the
    caller's personal org, and by-seq only resolves that one.
    """
    by_seq, by_ref, by_alias, by_id_shared = _routes()

    out = await _read(server, f"defernowork://item/{quote(APP_URL_SEQ, safe='')}")

    assert by_ref.called and by_ref.call_count == 1
    assert not by_seq.called
    assert not by_alias.called
    assert by_id_shared.called
    # The returned item carries the shared org's ref, not a personal-org one.
    assert out["ref"] == f"{SHARED_SLUG}-456"
    assert out["org_slug"] == SHARED_SLUG
    assert out["sequence"] == 456
    # Still a Compact projection.
    assert out["description"] == "the body"
    assert "comments" not in out


@respx.mock
async def test_app_url_with_uuid_tail_short_circuits_to_the_id_route(server):
    """An App URL whose last segment is a UUID resolves with no lookup round-trip."""
    by_seq, by_ref, by_alias, by_id_shared = _routes()

    out = await _read(server, f"defernowork://item/{quote(APP_URL_UUID, safe='')}")

    assert not by_seq.called
    assert not by_ref.called
    assert not by_alias.called
    assert by_id_shared.call_count == 1
    assert out["ref"] == f"{SHARED_SLUG}-456"


@respx.mock
@TYPED_RESOURCE_ERRORS
async def test_raw_app_url_never_reaches_the_handler(server):
    """A raw App URL fails template matching, so no request is ever issued.

    The error is defined: ``ValueError`` naming the unknown resource. A real MCP
    client has to send the percent-encoded form to get anywhere.
    """
    _routes()

    with pytest.raises(ValueError) as excinfo:
        await _read(server, f"defernowork://item/{APP_URL_SEQ}")

    assert "Unknown resource" in str(excinfo.value)
    assert not respx.calls


# ── Alias: encoded twice, once in the URI and once on the wire ───────────────


@respx.mock
async def test_alias_resolves_through_the_by_alias_route(server):
    """``owner/repo#N`` resolves once percent-encoded into the resource URI.

    Encoding happens twice over. The client encodes ``octo/repo#42`` to put it
    in the resource URI, the handler unquotes it, then ``get_item_by_alias``
    re-encodes it with ``safe=''`` for the outbound path. The backend therefore
    sees ``/items/by-alias/octo%2Frepo%2342``.
    """
    by_seq, by_ref, by_alias, by_id_shared = _routes()

    out = await _read(server, f"defernowork://item/{quote(ALIAS, safe='')}")

    assert by_alias.call_count == 1
    assert not by_seq.called
    assert not by_ref.called
    assert by_alias.calls[0].request.url.raw_path.decode().endswith(
        "/items/by-alias/octo%2Frepo%2342"
    )
    assert by_id_shared.called
    assert out["ref"] == f"{SHARED_SLUG}-456"


@respx.mock
@TYPED_RESOURCE_ERRORS
async def test_raw_alias_never_reaches_the_handler(server):
    """A raw Alias fails template matching on the ``/`` in ``owner/repo``."""
    _routes()

    with pytest.raises(ValueError) as excinfo:
        await _read(server, f"defernowork://item/{ALIAS}")

    assert "Unknown resource" in str(excinfo.value)
    assert not respx.calls


# ── Sequence shorthand stays on the personal org ─────────────────────────────


@pytest.mark.parametrize(
    ("label", "ref"),
    [
        ("hash prefixed, encoded", quote("#123", safe="")),
        ("bare digits", "123"),
    ],
)
@respx.mock
async def test_sequence_shorthand_routes_only_to_by_seq(server, label, ref):
    """Both Sequence spellings hit by-seq and nothing else.

    by-seq is personal-org only, by backend design. Pinning the negative routes
    is what makes that visible: a Sequence shorthand can never reach by-ref or
    by-alias, so it can never name an item in another org.
    """
    by_seq, by_ref, by_alias, _ = _routes()

    out = await _read(server, f"defernowork://item/{ref}")

    assert by_seq.call_count == 1
    assert not by_ref.called
    assert not by_alias.called
    assert out["ref"] == f"{PERSONAL_SLUG}-123"
    assert out["org_slug"] == PERSONAL_SLUG


@respx.mock
async def test_naming_a_shared_org_item_takes_a_canonical_ref(server):
    """The Canonical ref is the other way into a non-personal org.

    Sequence ``456`` would ask by-seq for the caller's own item 456. The
    Canonical ref ``acme-456`` names the org explicitly and routes by-ref.
    """
    by_seq, by_ref, by_alias, _ = _routes()

    out = await _read(server, f"defernowork://item/{SHARED_SLUG}-456")

    assert by_ref.call_count == 1
    assert not by_seq.called
    assert not by_alias.called
    assert out["org_slug"] == SHARED_SLUG


# ── malformed and empty refs produce defined errors ──────────────────────────
#
# FINDING, pinned deliberately. The resolver raises ``DefernoError(400)``, but
# nothing that reaches the caller is a DefernoError. ``ResourceTemplate.
# create_resource`` catches it and raises ``ValueError``, then
# ``ResourceManager.get_resource`` catches THAT and raises another ``ValueError``
# around it. The message prefix therefore appears twice and the typed status
# code is only reachable through the ``__context__`` chain. It is a defined
# failure, not a traceback out of the SDK, but the type is lost.


@pytest.mark.parametrize(
    ("label", "ref", "expected_in_message"),
    [
        # Ambiguous alias: collides with the Canonical ref shape, so the
        # classifier refuses to auto-route it.
        ("ambiguous alias", "ABC-223", "'ABC-223'"),
        ("unrecognized junk", "not-a-ref!!", "'not-a-ref!!'"),
        # Whitespace survives matching. ``classify_ref`` strips it to the empty
        # string and refuses it, but the message quotes the ref as received, so
        # the space is still visible in the error.
        ("encoded space", "%20", "' '"),
        ("literal space", " ", "' '"),
    ],
)
@TYPED_RESOURCE_ERRORS
@respx.mock
async def test_malformed_ref_raises_a_defined_error(
    server, label, ref, expected_in_message
):
    _routes()

    with pytest.raises(ValueError) as excinfo:
        await _read(server, f"defernowork://item/{ref}")

    message = str(excinfo.value)
    assert "is not an auto-routable Ref input form" in message
    assert expected_in_message in message
    # Double-wrapped: create_resource wraps, then get_resource wraps again.
    assert message.count("Error creating resource from template:") == 2
    # No lookup was attempted; the classifier refused before any HTTP.
    assert not respx.calls

    # The original typed error survives only in the implicit context chain.
    cause = excinfo.value.__context__.__context__
    assert isinstance(cause, DefernoError)
    assert cause.status_code == 400


@respx.mock
@TYPED_RESOURCE_ERRORS
async def test_empty_ref_is_an_unknown_resource(server):
    """``defernowork://item/`` fails matching rather than reaching the handler.

    The failure is defined (a ``ValueError`` naming the URI) but it comes from a
    different layer than every other bad ref, and it carries no 400 and no
    explanation of what a Ref input form is. RFC 6570 defines empty-value
    expansion, so SDK 2.x may route this to the handler instead, where the
    classifier would turn it into the same 400 as a whitespace ref.
    """
    _routes()

    with pytest.raises(ValueError) as excinfo:
        await _read(server, "defernowork://item/")

    assert str(excinfo.value) == "Unknown resource: defernowork://item/"
    assert not respx.calls
