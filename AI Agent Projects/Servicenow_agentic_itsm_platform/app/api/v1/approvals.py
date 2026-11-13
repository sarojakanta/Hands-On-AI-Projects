from fastapi import APIRouter, Depends, Query

from app.api.deps import Services, get_services, require_role
from app.api.schemas import ApprovalDecision, Page
from app.security.rbac import Principal

router = APIRouter(prefix="/approvals", tags=["approvals"])

STATUSES = "^(pending|approved|executed|failed|rejected|expired)$"


@router.get("", response_model=Page[dict], summary="List approval requests")
def list_approvals(status: str | None = Query(None, pattern=STATUSES), record_number: str | None = None,
                   limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
                   svc: Services = Depends(get_services), _: Principal = Depends(require_role("analyst"))):
    items = svc.approvals.list(status=status, record_number=record_number, limit=limit, offset=offset)
    return Page(items=items, total=svc.approvals.count(status), limit=limit, offset=offset)


@router.get("/{approval_id}", summary="Approval request with its audit trail")
def get_approval(approval_id: str, svc: Services = Depends(get_services),
                 _: Principal = Depends(require_role("analyst"))):
    return svc.approvals.get(approval_id)


@router.post("/{approval_id}/decision", summary="Approve or reject",
             description="The approver identity is always the authenticated user; it can not be supplied in the "
                         "request. Approving executes the approved change exactly as stored. Rejection needs a comment.")
def decide(approval_id: str, body: ApprovalDecision, svc: Services = Depends(get_services),
           principal: Principal = Depends(require_role("approver"))):
    return svc.approvals.decide(approval_id, principal, body.decision, body.comment)


@router.post("/{approval_id}/retry", summary="Retry execution of an approved action that failed")
def retry(approval_id: str, svc: Services = Depends(get_services),
          principal: Principal = Depends(require_role("approver"))):
    return svc.approvals.retry(approval_id, principal)
