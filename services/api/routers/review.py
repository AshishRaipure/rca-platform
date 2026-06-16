"""Review API: human approval decisions + feedback.

Recording an approval writes the decision and its audit row in one transaction, then asks the
orchestrator to resume the paused workflow. The decision itself performs no production action —
it gates whether the (advisory) output is released, and the platform still has no capability to
execute changes.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from fastapi import Path as PathParam

from services.api.auth import Principal, Role
from services.api.deps import AppDeps, get_app_deps, require_orchestrator, require_roles
from services.api.errors import NotFound
from services.api.schemas import (
    ApprovalDecisionRequest,
    ApprovalResponse,
    FeedbackRequest,
    FeedbackResponse,
)
from services.orchestrator.client import OrchestratorClient

router = APIRouter(prefix="/v1/investigations", tags=["review"])


@router.post("/{investigation_id}/approvals", status_code=201, response_model=ApprovalResponse)
async def record_approval(
    body: ApprovalDecisionRequest,
    investigation_id: UUID = PathParam(...),
    principal: Principal = Depends(require_roles(Role.approver)),
    deps: AppDeps = Depends(get_app_deps),
    orchestrator: OrchestratorClient = Depends(require_orchestrator),
) -> ApprovalResponse:
    async with deps.uow_factory(principal.scope) as uow:
        inv = await uow.investigations.get(investigation_id)
        if inv is None:
            raise NotFound("investigation not found", code="not_found")
        approval = await uow.approvals.create(
            investigation_id=investigation_id, decision=body.decision, target=body.target,
            target_id=body.target_id, comment=body.comment, decided_by=principal.subject,
            team_id=inv.team_id,
        )
        await uow.audit.record(
            category="approval", action="approval.recorded", actor_id=principal.subject,
            investigation_id=investigation_id, request_id=uuid4().hex,
            result_summary=f"decision={body.decision} target={body.target}",
            metadata={"target_id": body.target_id},
        )
        resp = ApprovalResponse(
            id=approval.id, investigation_id=investigation_id, decision=approval.decision,
            target=approval.target, target_id=approval.target_id, comment=approval.comment,
            decided_by=approval.decided_by, decided_at=approval.decided_at,
        )

    # resume the workflow only after the decision has been durably recorded
    status = await orchestrator.resume_after_approval(
        investigation_id=investigation_id, decision=body.decision, target=body.target,
        target_id=body.target_id, decided_by=principal.subject, scope=principal.scope,
    )
    async with deps.uow_factory(principal.scope) as uow:
        await uow.investigations.set_status(investigation_id, status.status)
    resp.workflow_status = status.status
    return resp


@router.post("/{investigation_id}/feedback", status_code=202, response_model=FeedbackResponse)
async def submit_feedback(
    body: FeedbackRequest,
    investigation_id: UUID = PathParam(...),
    principal: Principal = Depends(require_roles(Role.viewer, Role.responder, Role.approver)),
    deps: AppDeps = Depends(get_app_deps),
) -> FeedbackResponse:
    async with deps.uow_factory(principal.scope) as uow:
        inv = await uow.investigations.get(investigation_id)
        if inv is None:
            raise NotFound("investigation not found", code="not_found")
        fb = await uow.feedback.create(
            investigation_id=investigation_id, submitted_by=principal.subject, team_id=inv.team_id,
            rating=body.rating, useful=body.useful, category=body.category, comment=body.comment,
            target_id=body.target_id,
        )
        await uow.audit.record(
            category="feedback", action="feedback.submitted", actor_id=principal.subject,
            investigation_id=investigation_id, request_id=uuid4().hex,
            result_summary=f"useful={body.useful} rating={body.rating}",
        )
        return FeedbackResponse(id=fb.id, investigation_id=investigation_id, accepted=True)
