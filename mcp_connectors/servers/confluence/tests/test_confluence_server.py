"""Unit tests for the Confluence MCP integration.

Fake HTTP transport, no network. These assert the read-only guarantee, dispatch and validation
(including the ``id`` param Agent 2 sends to ``confluence.get_page``), HTML-to-text excerpting,
CQL construction, pagination/caps, and error mapping.

Run with: pytest -q  (requires pytest, pytest-asyncio, pydantic v2).
"""
from __future__ import annotations

import pytest

from mcp_connectors.servers.confluence.client import ConfluenceClient
from mcp_connectors.servers.confluence.config import ConfluenceConfig
from mcp_connectors.servers.confluence.server import ConfluenceMCPServer
from mcp_connectors.servers.confluence.tools import TOOL_SPECS


# --------------------------------------------------------------------------- fakes

class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._json


class FakeTransport:
    def __init__(self, queue=None, responder=None):
        self._queue = list(queue or [])
        self._responder = responder
        self.calls = 0
        self.requests = []

    async def get(self, url, *, headers, params, timeout):
        self.calls += 1
        self.requests.append((url, dict(params)))
        if self._responder is not None:
            return self._responder(url, dict(params))
        if not self._queue:
            raise RuntimeError("no fake response queued")
        return self._queue.pop(0)


def _server(transport, config=None):
    config = config or ConfluenceConfig()
    client = ConfluenceClient(
        transport, base_url="https://acme.atlassian.net/wiki",
        authorization="Basic test", config=config,
    )
    return ConfluenceMCPServer(client, config=config), client


def _content(cid="123", title="CPU runbook",
             body="<p>Restart the <strong>worker</strong> pool.</p>"):
    return {
        "id": cid, "type": "page", "title": title,
        "space": {"key": "OPS", "name": "Operations"},
        "version": {"number": 3, "when": "2026-06-01T10:00:00.000Z"},
        "body": {"storage": {"value": body, "representation": "storage"}},
        "ancestors": [{"id": "1", "title": "Home"}],
        "_links": {"webui": "/spaces/OPS/pages/123/CPU+runbook",
                   "base": "https://acme.atlassian.net/wiki"},
    }


def _attachment():
    return {
        "id": "att1", "title": "diagram.png",
        "extensions": {"mediaType": "image/png", "fileSize": 1234},
        "version": {"number": 1, "when": "2026-01-01T00:00:00Z"},
        "_links": {"download": "/download/attachments/123/diagram.png",
                   "base": "https://acme.atlassian.net/wiki"},
    }


# -------------------------------------------------------------------------- tests

def test_only_read_only_tools_are_registered():
    assert TOOL_SPECS and all(not s.mutates for s in TOOL_SPECS)
    write_verbs = ("create", "update", "delete", "remove", "put", "post", "patch", "move", "trash")
    for s in TOOL_SPECS:
        assert not any(v in s.name.lower() for v in write_verbs), s.name
    _, client = _server(FakeTransport())
    for method in ("post", "put", "delete", "patch"):
        assert not hasattr(client, method)


def test_list_tools_reports_read_only():
    server, _ = _server(FakeTransport())
    tools = server.list_tools()
    assert len(tools) == len(TOOL_SPECS)
    assert all(t["read_only"] for t in tools)


@pytest.mark.asyncio
async def test_get_page_accepts_id_and_strips_html():
    # Agent 2's freshness probe calls confluence.get_page with {"id": ...}
    tx = FakeTransport(queue=[FakeResponse(200, _content())])
    server, _ = _server(tx)
    res = await server.call_tool(tool="confluence.get_page", params={"id": "123"}, request_id="r")
    assert res.ok is True
    page = res.data["page"]
    assert page["id"] == "123"
    assert page["version"] == 3
    assert page["last_modified"] == "2026-06-01T10:00:00.000Z"
    assert "<p>" not in page["excerpt"] and "Restart the worker pool" in page["excerpt"]
    assert page["url"].endswith("/spaces/OPS/pages/123/CPU+runbook")


@pytest.mark.asyncio
async def test_search_builds_cql_from_text():
    def responder(url, params):
        assert "text ~" in params.get("cql", "")
        return FakeResponse(200, {"results": [_content()], "_links": {}})

    server, _ = _server(FakeTransport(responder=responder))
    res = await server.call_tool(tool="confluence.search", params={"text": "cpu"}, request_id="r")
    assert res.ok is True
    assert res.data["count"] == 1
    assert "cpu" in res.data["cql"]


@pytest.mark.asyncio
async def test_search_requires_cql_or_text():
    server, _ = _server(FakeTransport())
    res = await server.call_tool(tool="confluence.search", params={}, request_id="r")
    assert res.ok is False
    assert "invalid params" in res.error


@pytest.mark.asyncio
async def test_pagination_follows_links_next():
    def responder(url, params):
        if int(params.get("start", 0)) == 0:
            return FakeResponse(200, {"results": [_content("a"), _content("b")],
                                      "_links": {"next": "/rest/api/content?start=2"}})
        return FakeResponse(200, {"results": [_content("c")], "_links": {}})

    server, _ = _server(FakeTransport(responder=responder), ConfluenceConfig(page_limit=2))
    res = await server.call_tool(tool="confluence.list_pages",
                                 params={"space_key": "OPS"}, request_id="r")
    assert res.ok is True
    assert res.data["count"] == 3


@pytest.mark.asyncio
async def test_pagination_respects_max_items():
    def responder(url, params):  # always advertises another page
        return FakeResponse(200, {"results": [_content("a"), _content("b")],
                                  "_links": {"next": "/x"}})

    cfg = ConfluenceConfig(page_limit=2, max_items=3)
    server, _ = _server(FakeTransport(responder=responder), cfg)
    res = await server.call_tool(tool="confluence.list_pages",
                                 params={"space_key": "OPS"}, request_id="r")
    assert res.data["count"] == 3


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds():
    tx = FakeTransport(queue=[
        FakeResponse(429, {}, headers={"Retry-After": "0"}),
        FakeResponse(200, _content()),
    ])
    server, _ = _server(tx, ConfluenceConfig(backoff_base_s=0.0, max_retries=3))
    res = await server.call_tool(tool="confluence.get_page", params={"id": "123"}, request_id="r")
    assert res.ok is True
    assert tx.calls == 2


@pytest.mark.asyncio
async def test_not_found_maps_to_error():
    tx = FakeTransport(queue=[FakeResponse(404, {})])
    server, _ = _server(tx)
    res = await server.call_tool(tool="confluence.get_page", params={"id": "missing"}, request_id="r")
    assert res.ok is False
    assert "not found" in res.error.lower()


@pytest.mark.asyncio
async def test_auth_error_is_not_retried():
    tx = FakeTransport(queue=[FakeResponse(401, {})])
    server, _ = _server(tx, ConfluenceConfig(max_retries=3))
    res = await server.call_tool(tool="confluence.get_page", params={"id": "123"}, request_id="r")
    assert res.ok is False
    assert tx.calls == 1


@pytest.mark.asyncio
async def test_get_attachments_dispatch():
    tx = FakeTransport(queue=[FakeResponse(200, {"results": [_attachment()], "_links": {}})])
    server, _ = _server(tx)
    res = await server.call_tool(tool="confluence.get_attachments",
                                 params={"page_id": "123"}, request_id="r")
    assert res.ok is True
    assert res.data["count"] == 1
    assert res.data["attachments"][0]["media_type"] == "image/png"
    assert res.data["attachments"][0]["download_url"].endswith("/diagram.png")
