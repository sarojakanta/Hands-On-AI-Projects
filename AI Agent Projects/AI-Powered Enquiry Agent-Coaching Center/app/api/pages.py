"""HTML page routes: admin dashboard + widget demo."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_admin_optional
from app.config import get_settings
from app.database.session import get_db
from app.models import AdminUser
from app.services import course_service, institute_service

router = APIRouter(tags=["pages"])
settings = get_settings()

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
def widget_demo(
    request: Request,
    institute: str | None = None,
    db: Session = Depends(get_db),
):
    """Public demo page that embeds the chat widget.

    With multiple institutes, ``?institute=<slug>`` selects which one to show;
    all active institutes are listed so a visitor can switch between them.
    """
    institutes = institute_service.list_active(db)
    selected = None
    if institute:
        selected = next((i for i in institutes if i.slug == institute), None)
    if selected is None and institutes:
        selected = institutes[0]

    if selected is None:
        institute_name, api_key, slug = settings.app_name, "", ""
        courses = []
        tagline = ""
    else:
        institute_name, api_key, slug = selected.name, selected.api_key, selected.slug
        tagline = selected.description or ""
        courses = [
            {
                "name": c.name,
                "fee": c.fee,
                "currency": c.currency,
                "duration": c.duration,
                "mode": c.mode,
                "description": c.description,
                "eligibility": c.eligibility,
                "batches": [
                    {
                        "name": b.name,
                        "timing": b.timing,
                        "seats_available": b.seats_available,
                    }
                    for b in c.batches
                    if b.is_active
                ],
            }
            for c in course_service.list_courses(db, selected.id)
        ]

    return templates.TemplateResponse(
        "widget_demo.html",
        {
            "request": request,
            "institute_name": institute_name,
            "api_key": api_key,
            "slug": slug,
            "tagline": tagline,
            "courses": courses,
            "institutes": [
                {"name": i.name, "slug": i.slug} for i in institutes
            ],
        },
    )


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return templates.TemplateResponse("admin_login.html", {"request": request})


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    admin: AdminUser | None = Depends(get_current_admin_optional),
):
    if admin is None:
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse(
        "admin_dashboard.html",
        {"request": request, "admin_email": admin.email, "institute_id": admin.institute_id},
    )
