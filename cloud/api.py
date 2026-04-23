"""FastAPI control plane for Kontext cloud sync."""
from fastapi import Depends, FastAPI, Header, HTTPException

from cloud.auth import (
    extract_bearer,
    generate_workspace_token,
    verify_workspace_token,
)
from cloud.codec import unpack_payload
from cloud.models import (
    CreateWorkspaceRequest,
    DeviceEnrollmentRequest,
    HistoryEnvelope,
    PushRequest,
    RevokeDeviceRequest,
    SnapshotRequest,
)
from cloud.replay import apply_canonical_revision, apply_history_op
from db import KontextDB


def _active_device_rows(db, workspace_id: str) -> list[dict]:
    rows = db.conn.execute(
        "SELECT * FROM devices WHERE workspace_id = ? AND revoked_at IS NULL",
        (workspace_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _enforce_device_limit(db, workspace_id: str, requested_class: str, device_id: str) -> None:
    rows = _active_device_rows(db, workspace_id)
    if any(row["id"] == device_id for row in rows):
        return
    interactive = sum(1 for row in rows if row["device_class"] == "interactive")
    servers = sum(1 for row in rows if row["device_class"] == "server")
    if requested_class == "interactive" and interactive >= 2:
        raise HTTPException(status_code=409, detail="interactive device limit reached")
    if requested_class == "server" and servers >= 1:
        raise HTTPException(status_code=409, detail="server device limit reached")


def _require_active_device(db, workspace_id: str, device_id: str) -> dict:
    row = db.conn.execute(
        "SELECT * FROM devices WHERE workspace_id = ? AND id = ?",
        (workspace_id, device_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=403, detail="device is not enrolled")
    if row["revoked_at"] is not None:
        raise HTTPException(status_code=403, detail="device is revoked")
    return dict(row)


def _device_payload(row) -> dict:
    return {
        "device_label": row["label"] if row else "",
        "device_class": row["device_class"] if row else "interactive",
        "device_public_key": (
            row["public_key"].decode("utf-8", errors="ignore")
            if row and row["public_key"] is not None
            else ""
        ),
    }


def _authorize_workspace(db, workspace_id: str, authorization: str | None) -> None:
    record = db.get_workspace_token_record(workspace_id)
    if record is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    token = extract_bearer(authorization)
    if not token or not verify_workspace_token(token, record["salt"], record["hash"]):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": 'Bearer realm="kontext"'},
        )


def build_app(db) -> FastAPI:
    app = FastAPI(title="Kontext Cloud Control Plane")
    db_path = db.db_path

    def require_workspace_auth(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ) -> str:
        with KontextDB(db_path) as req_db:
            _authorize_workspace(req_db, workspace_id, authorization)
        return workspace_id

    def require_workspace_auth_body(
        body: PushRequest,
        authorization: str | None = Header(default=None),
    ) -> PushRequest:
        with KontextDB(db_path) as req_db:
            _authorize_workspace(req_db, body.workspace_id, authorization)
        return body

    @app.post("/v1/workspaces")
    def create_workspace(req: CreateWorkspaceRequest):
        with KontextDB(db_path) as req_db:
            existing = req_db.get_workspace_token_record(req.workspace_id)
            if existing is not None:
                if not req.workspace_token or not verify_workspace_token(
                    req.workspace_token, existing["salt"], existing["hash"]
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="workspace already exists; provide the original workspace_token to re-link",
                    )
                req_db.create_workspace(req.workspace_id, req.name, req.recovery_key_id)
                return {"workspace_id": req.workspace_id, "status": "ok"}

            token, salt_hex, hash_hex = generate_workspace_token()
            req_db.create_workspace(
                req.workspace_id,
                req.name,
                req.recovery_key_id,
                api_token_hash=hash_hex,
                api_token_salt=salt_hex,
            )
        return {
            "workspace_id": req.workspace_id,
            "workspace_token": token,
            "status": "ok",
        }

    @app.post("/v1/devices/enroll")
    def enroll_device(
        req: DeviceEnrollmentRequest,
        authorization: str | None = Header(default=None),
    ):
        with KontextDB(db_path) as req_db:
            _authorize_workspace(req_db, req.workspace_id, authorization)
            if req_db.is_device_revoked(req.device_id):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "device is revoked; issue a new device_id or use the privileged "
                        "rotate-credentials path to un-revoke"
                    ),
                )
            _enforce_device_limit(req_db, req.workspace_id, req.device_class, req.device_id)
            req_db.register_device(
                device_id=req.device_id,
                workspace_id=req.workspace_id,
                label=req.label,
                device_class=req.device_class,
                public_key=req.device_public_key.encode("utf-8"),
            )
        return {"device_id": req.device_id, "status": "ok"}

    @app.post("/v1/devices/revoke")
    def revoke_device(
        req: RevokeDeviceRequest,
        authorization: str | None = Header(default=None),
    ):
        with KontextDB(db_path) as req_db:
            _authorize_workspace(req_db, req.workspace_id, authorization)
            req_db.revoke_device(req.workspace_id, req.device_id)
        return {"device_id": req.device_id, "status": "revoked"}

    @app.post("/v1/snapshots/create")
    def create_snapshot(
        req: SnapshotRequest,
        authorization: str | None = Header(default=None),
    ):
        with KontextDB(db_path) as req_db:
            _authorize_workspace(req_db, req.workspace_id, authorization)
            _require_active_device(req_db, req.workspace_id, req.device_id)
            return req_db.create_snapshot(req.workspace_id)

    @app.get("/v1/snapshots/latest")
    def pull_latest_snapshot(
        workspace_id: str,
        device_id: str,
        authorization: str | None = Header(default=None),
    ):
        with KontextDB(db_path) as req_db:
            _authorize_workspace(req_db, workspace_id, authorization)
            _require_active_device(req_db, workspace_id, device_id)
            return {"snapshot": req_db.get_latest_snapshot(workspace_id)}

    @app.post("/v1/sync/push")
    def push_ops(req: PushRequest = Depends(require_workspace_auth_body)):
        with KontextDB(db_path) as req_db:
            for device_id in {item.device_id for item in req.items}:
                _require_active_device(req_db, req.workspace_id, device_id)

            if req.lane == "history":
                for item in req.items:
                    envelope = HistoryEnvelope(
                        op_id=item.op_id,
                        workspace_id=req.workspace_id,
                        device_id=item.device_id,
                        op_kind=item.op_kind,
                        entity_type=item.entity_type,
                        entity_id=item.entity_id,
                        created_at=item.created_at,
                        payload=item.payload,
                    )
                    apply_history_op(req_db, envelope)
            elif req.lane == "canonical":
                for item in req.items:
                    apply_canonical_revision(
                        req_db,
                        workspace_id=req.workspace_id,
                        object_id=item.entity_id,
                        object_type=item.entity_type,
                        revision_id=item.op_id,
                        parent_revision=item.parent_revision,
                        device_id=item.device_id,
                        payload=item.payload,
                        created_at=item.created_at,
                        accepted=bool(item.accepted),
                    )
            else:
                raise HTTPException(status_code=400, detail="unsupported sync lane")
        return {"accepted": len(req.items), "status": "ok"}

    @app.get("/v1/sync/pull")
    def pull_ops(
        workspace_id: str,
        device_id: str,
        lane: str,
        after: str = "",
        limit: int = 500,
        authorization: str | None = Header(default=None),
    ):
        with KontextDB(db_path) as req_db:
            _authorize_workspace(req_db, workspace_id, authorization)
            _require_active_device(req_db, workspace_id, device_id)

            if lane == "history":
                rows = req_db.list_history_ops_since(workspace_id, after, limit=limit)
                items = []
                for row in rows:
                    source_device = req_db.conn.execute(
                        "SELECT * FROM devices WHERE id = ? AND workspace_id = ?",
                        (row["device_id"], workspace_id),
                    ).fetchone()
                    items.append({
                        "op_id": row["id"],
                        "device_id": row["device_id"],
                        "op_kind": row["op_kind"],
                        "entity_type": row["entity_type"],
                        "entity_id": row["entity_id"],
                        "created_at": row["created_at"],
                        "payload": unpack_payload(row["payload"]),
                        **_device_payload(source_device),
                    })
            elif lane == "canonical":
                rows = req_db.list_canonical_revisions_since(workspace_id, after, limit=limit)
                items = []
                for row in rows:
                    source_device = req_db.conn.execute(
                        "SELECT * FROM devices WHERE id = ? AND workspace_id = ?",
                        (row["device_id"], workspace_id),
                    ).fetchone()
                    items.append({
                        "op_id": row["id"],
                        "device_id": row["device_id"],
                        "op_kind": "canonical.revision",
                        "entity_type": row["object_type"],
                        "entity_id": row["object_id"],
                        "created_at": row["created_at"],
                        "payload": unpack_payload(row["payload"]),
                        "parent_revision": row["parent_revision"],
                        "accepted": bool(row["accepted"]),
                        **_device_payload(source_device),
                    })
            else:
                raise HTTPException(status_code=400, detail="unsupported sync lane")

            if items:
                req_db.advance_sync_cursor(workspace_id, device_id, lane, items[-1]["op_id"])
        return {"items": items, "count": len(items)}

    # Dashboard settings endpoints (Anthropic API key, etc.).
    # Site sits behind Pangolin SSO, so no in-app auth is enforced here —
    # unauthenticated requests never reach the upstream.
    from cloud import config_store

    @app.get("/api/config", include_in_schema=False)
    def get_dashboard_config():
        key = config_store.get_anthropic_api_key()
        return {
            "anthropic_api_key_set": bool(key),
            "anthropic_api_key_masked": config_store.mask_key(key),
        }

    @app.post("/api/config", include_in_schema=False)
    def set_dashboard_config(body: dict):
        changed = False
        if "anthropic_api_key" in body:
            config_store.set_anthropic_api_key(body.get("anthropic_api_key"))
            changed = True
            # Drop snapshot cache so next /data.js reflects the new key
            # (cached per-entry synthesis stays — same content hash means
            # same synthesis, which is what we want; only new/changed
            # entries actually call Haiku).
            try:
                from cloud.dashboard_snapshot import _SNAPSHOT_CACHE
                _SNAPSHOT_CACHE["payload"] = None
                _SNAPSHOT_CACHE["built_at"] = 0.0
            except Exception:  # noqa: BLE001
                pass
        key = config_store.get_anthropic_api_key()
        return {
            "status": "ok",
            "changed": changed,
            "anthropic_api_key_set": bool(key),
            "anthropic_api_key_masked": config_store.mask_key(key),
        }

    from cloud.dashboard import register_dashboard
    register_dashboard(app, db_path)

    return app
