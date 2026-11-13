from fastapi import APIRouter, Depends, Query

from app.api.deps import Services, get_services, require_role
from app.api.schemas import Page
from app.schemas import ITSMRecord, WorkflowType
from app.security.rbac import Principal

router = APIRouter(prefix="/records", tags=["records"])


@router.get("/request/{record_id}/items", summary="Requested items (RITMs) of a service request")
def request_items(record_id: str, svc: Services = Depends(get_services),
                  _: Principal = Depends(require_role("viewer"))):
    return {"items": svc.servicenow.list_request_items(record_id)}


@router.get("/{workflow}", response_model=Page[ITSMRecord], summary="List/search ITSM records")
def list_records(workflow: WorkflowType, search: str = Query("", max_length=80),
                 limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
                 svc: Services = Depends(get_services), _: Principal = Depends(require_role("viewer"))):
    items, total = svc.servicenow.search_records(workflow, search, limit, offset)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{workflow}/{record_id}", response_model=ITSMRecord, summary="Get one record")
def get_record(workflow: WorkflowType, record_id: str, svc: Services = Depends(get_services),
               _: Principal = Depends(require_role("viewer"))):
    return svc.servicenow.get_record(workflow, record_id)
