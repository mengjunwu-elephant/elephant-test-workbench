from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator


def uid() -> str:
    return uuid4().hex


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1, max_length=200)
    phase: Literal["setup", "execute", "verify", "restore"] = "execute"
    kind: Literal["call", "read", "wait", "assert", "confirm", "method", "review"] = "call"
    api: str = ""
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    save_as: str = ""
    selector: list[str | int] = Field(default_factory=list)
    expected_exception: str = ""
    assertion: Literal["eq", "value_eq", "approx", "range", "type", "instance", "truthy"] = "eq"
    actual: Any = None
    expected: Any = None
    tolerance: float = Field(default=0.1, ge=0)
    timeout: float = Field(default=15, gt=0, le=3600)
    method: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class CaseDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=uid, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str = Field(min_length=1, max_length=200)
    model: str = "ultraarm_p1"
    description: str = ""
    category: Literal["normal", "boundary", "exception", "manual"] = "normal"
    sdk_requirement: str = ""
    method_versions: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    datasets: list[dict[str, Any]] = Field(default_factory=lambda: [{}])
    steps: list[Step] = Field(min_length=1, max_length=500)
    revision: int = Field(default=1, ge=1)
    status: Literal["draft", "shared"] = "draft"
    shared_id: str = Field(default="", pattern=r"^(?:[A-Za-z0-9_-]{1,128})?$")
    base_shared_revision: int = Field(default=0, ge=0)
    enabled: bool = True
    migration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sdk_requirement")
    @classmethod
    def validate_sdk_requirement(cls, value):
        from packaging.specifiers import SpecifierSet
        try:
            SpecifierSet(value)
        except ValueError as exc:
            raise ValueError("SDK 版本条件不合法，例如 >=4.0,<5") from exc
        return value

    @model_validator(mode="after")
    def validate_steps(self):
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError("步骤 ID 不可重复")
        if not self.datasets:
            raise ValueError("至少需要一组参数数据")
        if self.enabled and any(step.kind == 'review' for step in self.steps):
            raise ValueError('请先将迁移待完善步骤替换为实际流程，再启用用例')
        return self


class Connection(BaseModel):
    mode: Literal["hardware"] = "hardware"
    model: str = "ultraarm_p1"
    address: str = ""
    netport: int = Field(default=4500, ge=1, le=65535)
    connect_timeout: float = Field(default=8, gt=0, le=120)
    second_address: str = ""
    baudrate: int = Field(default=115200, gt=0)
    environment_id: str = ""
    arm: Literal["left", "right"] = "left"


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["cases", "plan", "latency", "aging", "debug", "connection"] = "cases"
    plan_id: str = ''
    plan_revision: int | None = None
    expected_fingerprint: str = ''
    parameter_overrides: dict[str, Any] = Field(default_factory=dict)
    connection: Connection = Field(default_factory=Connection)
    case_ids: list[str] = Field(default_factory=list)
    inline_cases: list[CaseDocument] = Field(default_factory=list)
    all_cases: bool = False
    include_manual: bool = False
    repeat: int = Field(default=1, ge=1, le=1000000)
    duration_seconds: float = Field(default=60, gt=0, le=31536000)
    interval_ms: float = Field(default=100, ge=0, le=3600000)
    max_consecutive_failures: int = Field(default=3, ge=1, le=100000)
    api: str = "get_angles_info"
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    measure_motion: bool = False
    timeout: float = Field(default=15, gt=0, le=3600)
    hardware_confirmed: bool = False


class HubLogin(BaseModel):
    username: str
    password: str


class PublishRequest(BaseModel):
    document: CaseDocument
    base_revision: int = Field(default=0, ge=0)


class PlanItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_id: str = Field(default_factory=uid)
    case_id: str
    content_hash: str = ''
    dataset_indices: list[int] = Field(default_factory=list)
    overrides: dict[str, Any] = Field(default_factory=dict)
    repeat: int = Field(default=1, ge=1, le=1000)


class PlanDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(default_factory=uid, pattern=r'^[A-Za-z0-9_-]{1,128}$')
    name: str = Field(min_length=1, max_length=200)
    model: str
    revision: int = Field(default=1, ge=1)
    description: str = ''
    archived: bool = False
    failure_policy: Literal['continue', 'stop'] = 'continue'
    items: list[PlanItem] = Field(min_length=1, max_length=500)
    definitions: dict[str, CaseDocument] = Field(default_factory=dict)

    @model_validator(mode='after')
    def unique_items(self):
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError('计划条目ID不可重复，同一用例重复加入需独立条目')
        return self

