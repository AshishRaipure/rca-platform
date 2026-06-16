"""Confluence read-only tool registry.

Every tool is a GET. The module-level assertion at the bottom fails import if any spec is ever
marked ``mutates=True`` — the structural guarantee that a write tool cannot be registered.
"""
from __future__ import annotations

from typing import Any

from mcp_connectors.contracts import ToolContext, ToolSpec
from mcp_connectors.servers.confluence.client import ConfluenceClient
from mcp_connectors.servers.confluence.errors import ConfluenceNotFoundError
from mcp_connectors.servers.confluence.schemas import (
    AttachmentsInput,
    CFAttachment,
    CFPage,
    CFSpace,
    ChildPagesInput,
    GetPageByTitleInput,
    GetPageInput,
    GetSpaceInput,
    ListPagesInput,
    SearchInput,
)


def _page_expand(body_format: str) -> str:
    return f"body.{body_format},version,space,history.lastUpdated,ancestors"


def _list_expand() -> str:
    # lighter expansion for list/search results (no body to keep results small)
    return "version,space,history.lastUpdated"


def _pages(rows: list, client: ConfluenceClient, body_format: str) -> list[dict[str, Any]]:
    return [
        CFPage.from_record(r, base_url=client.base_url, body_format=body_format,
                           snippet_max=client.config.snippet_max_chars).model_dump(mode="json")
        for r in rows
    ]


async def _h_get_page(client: ConfluenceClient, args: GetPageInput, ctx: ToolContext):
    body_format = args.body_format or client.config.default_body_format
    rec = await client.get_content_by_id(args.id, expand=_page_expand(body_format),
                                          timeout_s=ctx.timeout_s)
    if not rec:
        raise ConfluenceNotFoundError("page not found")
    page = CFPage.from_record(rec, base_url=client.base_url, body_format=body_format,
                              snippet_max=client.config.snippet_max_chars)
    return {"page": page.model_dump(mode="json")}


async def _h_get_page_by_title(client: ConfluenceClient, args: GetPageByTitleInput, ctx: ToolContext):
    body_format = args.body_format or client.config.default_body_format
    rec = await client.get_content_by_title(args.space_key, args.title,
                                            expand=_page_expand(body_format), timeout_s=ctx.timeout_s)
    if not rec:
        raise ConfluenceNotFoundError("page not found")
    page = CFPage.from_record(rec, base_url=client.base_url, body_format=body_format,
                              snippet_max=client.config.snippet_max_chars)
    return {"page": page.model_dump(mode="json")}


async def _h_search(client: ConfluenceClient, args: SearchInput, ctx: ToolContext):
    if args.cql:
        cql = args.cql
    else:
        safe = args.text.replace('"', " ")
        cql = f'type=page AND text ~ "{safe}"'
    rows = await client.search(cql=cql, expand=_list_expand(), limit=args.max_items,
                               timeout_s=ctx.timeout_s)
    pages = _pages(rows, client, client.config.default_body_format)
    return {"results": pages, "count": len(pages), "cql": cql}


async def _h_list_pages(client: ConfluenceClient, args: ListPagesInput, ctx: ToolContext):
    rows = await client.list_pages(args.space_key, expand=_list_expand(), limit=args.max_items,
                                   timeout_s=ctx.timeout_s)
    pages = _pages(rows, client, client.config.default_body_format)
    return {"pages": pages, "count": len(pages)}


async def _h_child_pages(client: ConfluenceClient, args: ChildPagesInput, ctx: ToolContext):
    rows = await client.get_child_pages(args.page_id, expand=_list_expand(), limit=args.max_items,
                                        timeout_s=ctx.timeout_s)
    pages = _pages(rows, client, client.config.default_body_format)
    return {"pages": pages, "count": len(pages)}


async def _h_attachments(client: ConfluenceClient, args: AttachmentsInput, ctx: ToolContext):
    rows = await client.get_attachments(args.page_id, limit=args.max_items, timeout_s=ctx.timeout_s)
    attachments = [CFAttachment.from_record(r, base_url=client.base_url).model_dump(mode="json")
                   for r in rows]
    return {"attachments": attachments, "count": len(attachments)}


async def _h_get_space(client: ConfluenceClient, args: GetSpaceInput, ctx: ToolContext):
    rec = await client.get_space(args.space_key, timeout_s=ctx.timeout_s)
    if not rec:
        raise ConfluenceNotFoundError("space not found")
    return {"space": CFSpace.from_record(rec).model_dump(mode="json")}


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(name="confluence.get_page",
             description="Fetch a Confluence page by id, with body + version/last-modified (read-only).",
             input_model=GetPageInput, handler=_h_get_page,
             read_scopes=("confluence:content:read",)),
    ToolSpec(name="confluence.get_page_by_title",
             description="Fetch a Confluence page by space key + title (read-only).",
             input_model=GetPageByTitleInput, handler=_h_get_page_by_title,
             read_scopes=("confluence:content:read",)),
    ToolSpec(name="confluence.search",
             description="Search Confluence pages by raw CQL or free text (read-only).",
             input_model=SearchInput, handler=_h_search,
             read_scopes=("confluence:content:read",)),
    ToolSpec(name="confluence.list_pages",
             description="List pages in a Confluence space, for ingestion enumeration (read-only).",
             input_model=ListPagesInput, handler=_h_list_pages,
             read_scopes=("confluence:content:read",)),
    ToolSpec(name="confluence.get_child_pages",
             description="List the child pages of a Confluence page, for tree traversal (read-only).",
             input_model=ChildPagesInput, handler=_h_child_pages,
             read_scopes=("confluence:content:read",)),
    ToolSpec(name="confluence.get_attachments",
             description="List attachments on a Confluence page (read-only).",
             input_model=AttachmentsInput, handler=_h_attachments,
             read_scopes=("confluence:content:read",)),
    ToolSpec(name="confluence.get_space",
             description="Fetch Confluence space metadata (read-only).",
             input_model=GetSpaceInput, handler=_h_get_space,
             read_scopes=("confluence:space:read",)),
]

# STRUCTURAL GUARANTEE: a mutating tool can never be registered for this connector.
assert all(not spec.mutates for spec in TOOL_SPECS), "Confluence connector must expose read-only tools only"
