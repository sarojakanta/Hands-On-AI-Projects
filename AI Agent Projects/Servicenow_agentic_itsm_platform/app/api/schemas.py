"""Pydantic request/response models for the v1 API."""
from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

from app.schemas import LLMProvider, WorkflowType

T = TypeVar("T")
Role = Literal["viewer", "analyst", "approver", "admin"]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int | None = None
    limit: int
    offset: int


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str
    roles: list[str]


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.@\-]+$")
    password: str = Field(min_length=8, max_length=72)
    roles: list[Role] = Field(min_length=1)
    email: str = Field(default="", max_length=255)


class UserUpdate(BaseModel):
    roles: list[Role] | None = Field(default=None, min_length=1)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=72)


class InvestigationCreate(BaseModel):
    record_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$",
                           description="ServiceNow record number or sys_id")
    workflow: WorkflowType = "incident"
    llm_provider: LLMProvider | None = None
    diagnostics: dict[str, str] = Field(default_factory=dict, description="Optional operator-supplied evidence")
    force: bool = Field(default=False, description="Start a new run even if one awaits approval")

    @field_validator("diagnostics")
    @classmethod
    def _limit_diagnostics(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 25 or any(len(k) > 100 or len(val) > 4000 for k, val in v.items()):
            raise ValueError("diagnostics: at most 25 entries, keys <= 100 and values <= 4000 characters")
        return v


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=2000)


class AgentUpdate(BaseModel):
    enabled: bool | None = None
    max_retries: int | None = Field(default=None, ge=0, le=5)
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    llm_provider: LLMProvider | None = None
    llm_model: str | None = Field(default=None, max_length=100)
    clear_llm_override: bool = False


class KnowledgeSearch(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)
    filters: dict[str, Any] = Field(default_factory=dict)


class LLMTest(BaseModel):
    provider: LLMProvider = "mock"
    model: str | None = Field(default=None, max_length=100)
