"""Architecture Discovery Agent (Agent 3) — core implementation.

Deterministic and read-only: it reads the pre-built topology/CMDB and recent changes through the
MCP gateway (read-only tools only) and assembles a dependency context for the RCA agent. It never
generates topology and never mutates anything; on tool failure it degrades (never crashes).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from agents.architecture_discovery.config import ArchitectureConfig
from agents.architecture_discovery.errors import ArchitectureInputError
from agents.architecture_discovery.schemas import (
    ArchitectureContext,
    ArchitectureNodeInfo,
    ArchitectureInput,
    DependencyEdge,
    RecentChange,
)
from agents.base.interfaces import AuditSink, Clock, MCPGateway
from agents.base.parsing import SystemClock

logger = logging.getLogger("agents.architecture_discovery")

AGENT_NAME = "architecture_discovery"


def _as_record(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        rec = data.get("ci") or data.get("record") or data.get("result") or data
        return rec if isinstance(rec, dict) else {}
    return {}


def _as_list(data: Any, *keys: str) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


class ArchitectureDiscoveryAgent:
    AGENT_NAME = AGENT_NAME

    def __init__(
        self, *, gateway: Optional[MCPGateway], audit: AuditSink,
        config: Optional[ArchitectureConfig] = None, clock: Optional[Clock] = None,
    ) -> None:
        self._gateway = gateway
        self._audit = audit
        self._config = config or ArchitectureConfig()
        self._clock = clock or SystemClock()

    async def run(
        self, request: ArchitectureInput, *, request_id: str,
        scope: Optional[dict[str, Any]] = None,
    ) -> ArchitectureContext:
        if not isinstance(request, ArchitectureInput):
            raise ArchitectureInputError("request must be an ArchitectureInput")
        scope = scope or {}
        warnings: list[str] = []
        impacted: list[ArchitectureNodeInfo] = []
        deps: list[DependencyEdge] = []
        changes: list[RecentChange] = []

        for name in request.affected_systems[: self._config.max_systems]:
            ci = await self._call("servicenow.get_cmdb_ci", {"name": name}, scope, request_id, warnings)
            rec = _as_record(ci)
            ci_id = rec.get("sys_id") or rec.get("ci_id")
            impacted.append(ArchitectureNodeInfo(
                ci_id=ci_id, name=rec.get("name") or name,
                ci_class=rec.get("sys_class_name") or rec.get("ci_class"),
                environment=rec.get("environment") or rec.get("u_environment"),
                status=rec.get("install_status") or rec.get("status"),
            ))
            if ci_id:
                rels = await self._call(
                    "servicenow.get_ci_relationships", {"sys_id": ci_id}, scope, request_id, warnings)
                for r in _as_list(rels, "relationships", "results"):
                    if not isinstance(r, dict):
                        continue
                    target = r.get("target") or r.get("name") or r.get("related_ci")
                    if target:
                        deps.append(DependencyEdge(
                            source=rec.get("name") or name, target=str(target),
                            relationship=r.get("type") or r.get("relationship") or "depends_on"))

        if self._config.include_changes:
            ch = await self._call(
                "servicenow.list_change_requests",
                {"limit": self._config.max_recent_changes}, scope, request_id, warnings)
            for c in _as_list(ch, "changes", "results", "records"):
                if not isinstance(c, dict):
                    continue
                cid = c.get("number") or c.get("sys_id") or c.get("change_id")
                if cid:
                    changes.append(RecentChange(
                        change_id=str(cid), summary=c.get("short_description") or c.get("summary"),
                        state=c.get("state"), when=c.get("closed_at") or c.get("end_date"),
                        risk=c.get("risk")))

        degraded = not impacted and not deps and not changes
        ctx = ArchitectureContext(
            impacted=impacted, dependencies=deps[: self._config.max_dependencies],
            recent_changes=changes[: self._config.max_recent_changes],
            summary=self._summary(impacted, deps, changes),
            topology_freshness="fresh" if impacted else "unknown",
            degraded=degraded, warnings=warnings,
        )
        await self._safe_audit(request, request_id, ctx)
        return ctx

    async def _call(self, tool, params, scope, request_id, warnings) -> Any:
        if self._gateway is None:
            warnings.append(f"{tool}: gateway unavailable")
            return None
        try:
            res = await self._gateway.call(
                tool=tool, params=params, scope=scope, request_id=request_id,
                timeout_s=self._config.tool_timeout_s)
            if not getattr(res, "ok", False):
                warnings.append(f"{tool}: {getattr(res, 'error', 'failed')}")
                return None
            return res.data
        except Exception as exc:  # read-only tool failure is survivable
            warnings.append(f"{tool}: {exc}")
            return None

    @staticmethod
    def _summary(impacted, deps, changes) -> str:
        names = ", ".join(n.name for n in impacted) or "unknown systems"
        parts = [f"Impacted: {names}."]
        if deps:
            parts.append(f"{len(deps)} dependency relationship(s) mapped.")
        if changes:
            parts.append(f"{len(changes)} recent change(s) found for correlation.")
        return " ".join(parts)

    async def _safe_audit(self, request, request_id, ctx) -> None:
        try:
            await self._audit.record(
                category="agent_output", action="architecture.completed", actor_id=AGENT_NAME,
                investigation_id=request.investigation_id, request_id=request_id,
                result_summary=(f"impacted={len(ctx.impacted)} deps={len(ctx.dependencies)} "
                                f"changes={len(ctx.recent_changes)}"),
                metadata={"degraded": ctx.degraded, "prompt_version": self._config.prompt_version})
        except Exception:
            logger.warning("architecture audit failed", exc_info=True)
