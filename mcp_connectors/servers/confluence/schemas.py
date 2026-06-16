"""Confluence tool input schemas + projections.

Projections trim Confluence content objects to what the platform uses and derive a plain-text
excerpt from the (HTML/XHTML) body for quick reading. The full raw body is retained for the
ingestion pipeline to chunk.
"""
from __future__ import annotations

import html
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(value: Optional[str], max_chars: int) -> str:
    """Best-effort HTML/XHTML -> plain text for excerpts (not a full macro renderer)."""
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_chars]


# ------------------------------------------------------------------- tool inputs

class GetPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Agent 2's freshness probe calls confluence.get_page with {"id": <page id>}
    id: str = Field(min_length=1)
    body_format: Optional[Literal["storage", "view"]] = None


class GetPageByTitleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body_format: Optional[Literal["storage", "view"]] = None


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cql: Optional[str] = None       # raw CQL
    text: Optional[str] = None      # convenience: builds a text ~ "..." CQL
    max_items: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one_of(self):
        if not self.cql and not self.text:
            raise ValueError("provide either 'cql' or 'text'")
        return self


class ListPagesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space_key: str = Field(min_length=1)
    max_items: Optional[int] = Field(default=None, ge=1)


class ChildPagesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: str = Field(min_length=1)
    max_items: Optional[int] = Field(default=None, ge=1)


class AttachmentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: str = Field(min_length=1)
    max_items: Optional[int] = Field(default=None, ge=1)


class GetSpaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space_key: str = Field(min_length=1)


# --------------------------------------------------------------- projections

class CFAncestor(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = None


class CFPage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    type: Optional[str] = None
    title: Optional[str] = None
    space_key: Optional[str] = None
    space_name: Optional[str] = None
    version: Optional[int] = None
    last_modified: Optional[str] = None
    url: Optional[str] = None
    excerpt: str = ""
    body: Optional[str] = None
    ancestors: list[CFAncestor] = Field(default_factory=list)

    @classmethod
    def from_record(cls, rec: dict[str, Any], *, base_url: str, body_format: str,
                    snippet_max: int) -> "CFPage":
        links = rec.get("_links") or {}
        space = rec.get("space") or {}
        version = rec.get("version") or {}
        body = (rec.get("body") or {}).get(body_format) or {}
        raw_body = body.get("value")
        last_modified = version.get("when") or (
            ((rec.get("history") or {}).get("lastUpdated") or {}).get("when")
        )
        webui = links.get("webui")
        base = links.get("base") or base_url
        return cls(
            id=rec.get("id"), type=rec.get("type"), title=rec.get("title"),
            space_key=space.get("key"), space_name=space.get("name"),
            version=version.get("number"), last_modified=last_modified,
            url=f"{base}{webui}" if webui else None,
            excerpt=strip_html(raw_body, snippet_max), body=raw_body,
            ancestors=[CFAncestor(id=a.get("id"), title=a.get("title"))
                       for a in (rec.get("ancestors") or [])],
        )


class CFAttachment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    title: Optional[str] = None
    media_type: Optional[str] = None
    file_size: Optional[int] = None
    download_url: Optional[str] = None
    version: Optional[int] = None
    last_modified: Optional[str] = None

    @classmethod
    def from_record(cls, rec: dict[str, Any], *, base_url: str) -> "CFAttachment":
        links = rec.get("_links") or {}
        ext = rec.get("extensions") or {}
        version = rec.get("version") or {}
        download = links.get("download")
        base = links.get("base") or base_url
        return cls(
            id=rec.get("id"), title=rec.get("title"),
            media_type=ext.get("mediaType"), file_size=ext.get("fileSize"),
            download_url=f"{base}{download}" if download else None,
            version=version.get("number"), last_modified=version.get("when"),
        )


class CFSpace(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "CFSpace":
        desc = (((rec.get("description") or {}).get("plain") or {}).get("value"))
        return cls(key=rec.get("key"), name=rec.get("name"), type=rec.get("type"),
                   description=desc)
