"""Typed models for cloud sync envelopes and API requests."""
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel


@dataclass(slots=True)
class HistoryEnvelope:
    op_id: str
    workspace_id: str
    device_id: str
    op_kind: str
    entity_type: str
    entity_id: str
    created_at: str
    payload: dict[str, Any]


class EnrollmentRequest(BaseModel):
    workspace_id: str
    enrollment_code: str
    label: str
    device_class: Literal["interactive", "server"]
    device_public_key: str


class CreateWorkspaceRequest(BaseModel):
    workspace_id: str
    name: str
    recovery_key_id: str
    workspace_token: str | None = None


class DeviceEnrollmentRequest(EnrollmentRequest):
    device_id: str


class RevokeDeviceRequest(BaseModel):
    workspace_id: str
    device_id: str


class SnapshotRequest(BaseModel):
    workspace_id: str
    device_id: str


class PushItem(BaseModel):
    op_id: str
    device_id: str
    op_kind: str
    entity_type: str
    entity_id: str
    created_at: str
    payload: dict[str, Any]
    parent_revision: str | None = None
    accepted: bool = False


class PushRequest(BaseModel):
    workspace_id: str
    lane: Literal["history", "canonical"]
    items: list[PushItem]