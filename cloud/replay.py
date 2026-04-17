"""Replay helpers for applying cloud sync payloads into the local DB."""
from cloud.codec import pack_payload


def _project_history_op(db, envelope) -> None:
    """Project replayed history ops into queryable local tables."""
    payload = envelope.payload or {}
    if envelope.op_kind == "entry.written":
        db.add_entry(
            file=payload.get("file", ""),
            fact=payload.get("fact", ""),
            source=payload.get("source", ""),
            grade=payload.get("grade", 5),
            tier=payload.get("tier", "active"),
            emit_cloud=False,
        )
    elif envelope.op_kind == "session.saved":
        db.save_session(
            project=payload.get("project", ""),
            status=payload.get("status", ""),
            next_step=payload.get("next_step", ""),
            key_decisions=payload.get("key_decisions", ""),
            summary=payload.get("summary", ""),
            files_touched=payload.get("files_touched", ""),
            workspace=payload.get("workspace", ""),
            emit_cloud=False,
        )
    elif envelope.op_kind == "prompt.logged":
        db.add_user_prompt(
            session_id=payload.get("session_id", ""),
            content=payload.get("content", ""),
            emit_cloud=False,
        )
    elif envelope.op_kind == "tool.logged":
        db.add_tool_event(
            session_id=payload.get("session_id", ""),
            tool_name=payload.get("tool_name", ""),
            summary=payload.get("summary", ""),
            file_path=payload.get("file_path"),
            grade=payload.get("grade", 5.0),
            emit_cloud=False,
        )


def apply_history_op(db, envelope) -> None:
    """Apply one history envelope idempotently to the local store."""
    with db.transaction():
        inserted = db.append_history_op(
            op_id=envelope.op_id,
            workspace_id=envelope.workspace_id,
            device_id=envelope.device_id,
            op_kind=envelope.op_kind,
            entity_type=envelope.entity_type,
            entity_id=envelope.entity_id,
            payload=pack_payload(envelope.payload),
            created_at=envelope.created_at,
        )
        if inserted:
            _project_history_op(db, envelope)
        db._execute(
            "UPDATE history_ops SET applied_at = COALESCE(applied_at, datetime('now')) WHERE id = ?",
            (envelope.op_id,),
        )


def apply_canonical_revision(db, workspace_id: str, object_id: str,
                             object_type: str, revision_id: str,
                             parent_revision: str | None, device_id: str,
                             payload: dict, created_at: str,
                             accepted: bool) -> None:
    """Apply a canonical revision using the current DB canonical contract."""
    db.append_canonical_revision(
        workspace_id=workspace_id,
        object_id=object_id,
        object_type=object_type,
        revision_id=revision_id,
        parent_revision=parent_revision,
        device_id=device_id,
        payload=pack_payload(payload),
        created_at=created_at,
        accepted=accepted,
    )