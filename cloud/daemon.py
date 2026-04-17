'''Local cloud-sync state and one-shot sync helpers.'''
import json
import uuid
from pathlib import Path

from cloud.client import CloudClient
from cloud.codec import unpack_payload
from cloud.keystore import (
    generate_device_keypair,
    load_workspace_token,
    save_device_private_key,
    save_workspace_token,
)
from cloud.manifest import validate_manifest
from cloud.models import HistoryEnvelope
from cloud.replay import apply_canonical_revision, apply_history_op
from db import LATEST_SCHEMA_VERSION


DEFAULT_EMBEDDING_MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
DEFAULT_RANKING_VERSION = 'v1'
DEFAULT_PROMPT_ROUTING_VERSION = 'v1'


def _state_path(db) -> Path:
    return Path(db.db_path).with_suffix('.cloud.json')


def _load_state(db) -> dict:
    path = _state_path(db)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(db, state: dict) -> None:
    path = _state_path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding='utf-8')


def _current_cursor(db, workspace_id: str, device_id: str, lane: str = 'history') -> str:
    row = db.conn.execute(
        'SELECT cursor FROM sync_cursors WHERE workspace_id = ? AND device_id = ? AND lane = ?',
        (workspace_id, device_id, lane),
    ).fetchone()
    return row['cursor'] if row else ''


def _history_payload_item(row: dict) -> dict:
    return {
        'op_id': row['id'],
        'device_id': row['device_id'],
        'op_kind': row['op_kind'],
        'entity_type': row['entity_type'],
        'entity_id': row['entity_id'],
        'created_at': row['created_at'],
        'payload': unpack_payload(row['payload']),
    }


def _canonical_payload_item(row: dict) -> dict:
    return {
        'op_id': row['id'],
        'device_id': row['device_id'],
        'op_kind': 'canonical.revision',
        'entity_type': row['object_type'],
        'entity_id': row['object_id'],
        'created_at': row['created_at'],
        'payload': unpack_payload(row['payload']),
        'parent_revision': row.get('parent_revision'),
        'accepted': bool(row.get('accepted')),
    }


def _local_manifest() -> dict:
    return {
        'schema_version': LATEST_SCHEMA_VERSION,
        'embedding_model': DEFAULT_EMBEDDING_MODEL,
        'ranking_version': DEFAULT_RANKING_VERSION,
        'prompt_routing_version': DEFAULT_PROMPT_ROUTING_VERSION,
    }


def _require_state(db) -> dict:
    state = _load_state(db)
    required = ('server_url', 'workspace_id', 'device_id')
    if not all(state.get(key) for key in required):
        raise ValueError('cloud sync is not linked')
    return state


def _authed_client(state: dict, keystore_root=None) -> CloudClient:
    client = CloudClient(
        state['server_url'],
        allow_insecure=bool(state.get('allow_insecure')),
    )
    root = keystore_root
    if root is None and state.get('keystore_root'):
        root = Path(state['keystore_root'])
    token = load_workspace_token(state['workspace_id'], root=root)
    if token:
        client.set_token(token)
    return client


def _sync_source_device(db, workspace_id: str, item: dict) -> None:
    device_id = str(item.get('device_id', '')).strip()
    if not device_id:
        return
    db.register_device(
        device_id=device_id,
        workspace_id=workspace_id,
        label=item.get('device_label') or device_id,
        device_class=item.get('device_class') or 'interactive',
        public_key=str(item.get('device_public_key', '')).encode('utf-8'),
    )


def _pull_history(db, client: CloudClient, state: dict, after: str,
                  limit: int) -> tuple[int, str]:
    pulled = 0
    cursor = after
    while True:
        response = client.pull_history(
            state['workspace_id'],
            state['device_id'],
            after=cursor,
            limit=limit,
        )
        items = response.get('items', [])
        if not items:
            return pulled, cursor

        for item in items:
            _sync_source_device(db, state['workspace_id'], item)
            apply_history_op(
                db,
                HistoryEnvelope(
                    op_id=item['op_id'],
                    workspace_id=state['workspace_id'],
                    device_id=item['device_id'],
                    op_kind=item['op_kind'],
                    entity_type=item['entity_type'],
                    entity_id=item['entity_id'],
                    created_at=item['created_at'],
                    payload=item.get('payload', {}),
                ),
            )

        cursor = items[-1]['op_id']
        db.advance_sync_cursor(state['workspace_id'], state['device_id'], 'history', cursor)
        pulled += len(items)
        if len(items) < limit:
            return pulled, cursor


def _pull_canonical(db, client: CloudClient, state: dict, after: str,
                    limit: int) -> tuple[int, str]:
    pulled = 0
    cursor = after
    while True:
        response = client.pull_canonical(
            state['workspace_id'],
            state['device_id'],
            after=cursor,
            limit=limit,
        )
        items = response.get('items', [])
        if not items:
            return pulled, cursor

        for item in items:
            _sync_source_device(db, state['workspace_id'], item)
            apply_canonical_revision(
                db,
                workspace_id=state['workspace_id'],
                object_id=item['entity_id'],
                object_type=item['entity_type'],
                revision_id=item['op_id'],
                parent_revision=item.get('parent_revision'),
                device_id=item['device_id'],
                payload=item.get('payload', {}),
                created_at=item['created_at'],
                accepted=bool(item.get('accepted')),
            )

        cursor = items[-1]['op_id']
        db.advance_sync_cursor(state['workspace_id'], state['device_id'], 'canonical', cursor)
        pulled += len(items)
        if len(items) < limit:
            return pulled, cursor


def _projection_is_empty(db) -> bool:
    tables = ('entries', 'sessions', 'user_prompts', 'tool_events', 'canonical_revisions')
    for table in tables:
        if db.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:
            return False
    return True


def _restore_snapshot(db, state: dict, snapshot: dict) -> int:
    remote_manifest = snapshot.get('manifest') or {}
    required_keys = {
        'schema_version',
        'embedding_model',
        'ranking_version',
        'prompt_routing_version',
    }
    if required_keys.issubset(remote_manifest.keys()):
        validate_manifest(_local_manifest(), remote_manifest)

    restored = db.restore_workspace_snapshot(snapshot)
    if snapshot.get('history_cursor'):
        db.advance_sync_cursor(state['workspace_id'], state['device_id'], 'history', snapshot['history_cursor'])
    if snapshot.get('canonical_cursor'):
        db.advance_sync_cursor(state['workspace_id'], state['device_id'], 'canonical', snapshot['canonical_cursor'])
    return restored

def get_status(db) -> dict:
    state = _load_state(db)
    if not state:
        return {'linked': False}

    workspace_id = state.get('workspace_id', '')
    device_id = state.get('device_id', '')
    workspace = db.conn.execute(
        'SELECT * FROM workspaces WHERE id = ?',
        (workspace_id,),
    ).fetchone()
    device = db.conn.execute(
        'SELECT * FROM devices WHERE id = ?',
        (device_id,),
    ).fetchone()
    history_count = db.conn.execute(
        'SELECT COUNT(*) FROM history_ops WHERE workspace_id = ?',
        (workspace_id,),
    ).fetchone()[0]
    canonical_count = db.conn.execute(
        '''
        SELECT COUNT(*)
          FROM canonical_revisions r
          JOIN canonical_objects o ON o.id = r.object_id
         WHERE o.workspace_id = ?
        ''',
        (workspace_id,),
    ).fetchone()[0]
    return {
        'linked': True,
        'server_url': state.get('server_url', ''),
        'workspace_id': workspace_id,
        'workspace_name': workspace['name'] if workspace else state.get('workspace_name', ''),
        'device_id': device_id,
        'label': device['label'] if device else state.get('label', ''),
        'device_class': device['device_class'] if device else state.get('device_class', ''),
        'cursor': _current_cursor(db, workspace_id, device_id, 'history'),
        'canonical_cursor': _current_cursor(db, workspace_id, device_id, 'canonical'),
        'history_count': history_count,
        'canonical_count': canonical_count,
    }


def link_workspace(db, server_url: str, workspace_id: str, label: str,
                   device_class: str = 'interactive', device_id: str | None = None,
                   workspace_name: str | None = None,
                   recovery_key_id: str | None = None,
                   workspace_token: str | None = None,
                   allow_insecure: bool = False,
                   keystore_root=None) -> dict:
    if not server_url.strip():
        raise ValueError('server_url is required')
    if not workspace_id.strip():
        raise ValueError('workspace_id is required')
    if not label.strip():
        raise ValueError('label is required')

    device_id = device_id or f'device-{uuid.uuid4().hex[:12]}'
    workspace_name = workspace_name or workspace_id
    recovery_key_id = recovery_key_id or f'recovery-{workspace_id}'

    private_key_bytes, public_key_bytes = generate_device_keypair()
    device_public_key_hex = public_key_bytes.hex()
    save_device_private_key(workspace_id, device_id, private_key_bytes, root=keystore_root)

    client = CloudClient(server_url, allow_insecure=allow_insecure)
    if workspace_token:
        issued_token = workspace_token
        client.create_workspace(workspace_id, workspace_name, recovery_key_id,
                                existing_token=workspace_token)
    else:
        create_response = client.create_workspace(workspace_id, workspace_name, recovery_key_id)
        issued_token = create_response.get('workspace_token', '')
        if not issued_token:
            raise RuntimeError('cloud server did not return workspace_token')

    client.set_token(issued_token)
    save_workspace_token(workspace_id, issued_token, root=keystore_root)

    client.enroll_device(
        workspace_id,
        device_id,
        label,
        device_class,
        device_public_key_hex,
    )

    manifest = _local_manifest()
    db.create_workspace(workspace_id, workspace_name, recovery_key_id)
    db.upsert_sync_manifest(
        workspace_id=workspace_id,
        schema_version=manifest['schema_version'],
        embedding_model=manifest['embedding_model'],
        ranking_version=manifest['ranking_version'],
        prompt_routing_version=manifest['prompt_routing_version'],
    )
    db.register_device(
        device_id=device_id,
        workspace_id=workspace_id,
        label=label,
        device_class=device_class,
        public_key=device_public_key_hex.encode('utf-8'),
    )
    state_payload = {
        'server_url': server_url.rstrip('/'),
        'workspace_id': workspace_id,
        'workspace_name': workspace_name,
        'device_id': device_id,
        'label': label,
        'device_class': device_class,
        'recovery_key_id': recovery_key_id,
        'allow_insecure': bool(allow_insecure),
    }
    if keystore_root is not None:
        state_payload['keystore_root'] = str(Path(keystore_root))
    _save_state(db, state_payload)
    status = get_status(db)
    if not workspace_token:
        status['workspace_token'] = issued_token
    return status


def create_snapshot(db) -> dict:
    state = _require_state(db)
    client = _authed_client(state)
    return client.create_snapshot(state['workspace_id'], state['device_id'])

def cloud_pull_once(db, limit: int = 500) -> int:
    state = _load_state(db)
    if not state:
        return 0
    client = _authed_client(state)
    history_pulled, _history_cursor = _pull_history(
        db,
        client,
        state,
        after=_current_cursor(db, state['workspace_id'], state['device_id'], 'history'),
        limit=limit,
    )
    canonical_pulled, _canonical_cursor = _pull_canonical(
        db,
        client,
        state,
        after=_current_cursor(db, state['workspace_id'], state['device_id'], 'canonical'),
        limit=limit,
    )
    return history_pulled + canonical_pulled


def sync_once(db, limit: int = 500) -> dict:
    state = _require_state(db)
    client = _authed_client(state)

    history_rows = db.list_history_ops_since(state['workspace_id'], '')
    history_items = [_history_payload_item(row) for row in history_rows]
    if history_items:
        client.push_history(state['workspace_id'], history_items)

    canonical_rows = db.list_canonical_revisions_since(state['workspace_id'], '')
    canonical_items = [_canonical_payload_item(row) for row in canonical_rows]
    if canonical_items:
        client.push_canonical(state['workspace_id'], canonical_items)

    history_pulled, history_cursor = _pull_history(
        db,
        client,
        state,
        after=_current_cursor(db, state['workspace_id'], state['device_id'], 'history'),
        limit=limit,
    )
    canonical_pulled, canonical_cursor = _pull_canonical(
        db,
        client,
        state,
        after=_current_cursor(db, state['workspace_id'], state['device_id'], 'canonical'),
        limit=limit,
    )
    return {
        'linked': True,
        'pushed': len(history_items) + len(canonical_items),
        'pulled': history_pulled + canonical_pulled,
        'cursor': history_cursor or canonical_cursor,
        'history_pushed': len(history_items),
        'canonical_pushed': len(canonical_items),
        'history_pulled': history_pulled,
        'canonical_pulled': canonical_pulled,
        'history_cursor': history_cursor,
        'canonical_cursor': canonical_cursor,
    }


def recover_workspace(db, limit: int = 500) -> dict:
    state = _require_state(db)
    client = _authed_client(state)

    snapshot = None
    snapshot_rows = 0
    history_after = ''
    canonical_after = ''
    if _projection_is_empty(db):
        snapshot_response = client.pull_latest_snapshot(state['workspace_id'], state['device_id'])
        snapshot = snapshot_response.get('snapshot')
        if snapshot:
            snapshot_rows = _restore_snapshot(db, state, snapshot)
            history_after = snapshot.get('history_cursor', '')
            canonical_after = snapshot.get('canonical_cursor', '')

    history_recovered, history_cursor = _pull_history(db, client, state, after=history_after, limit=limit)
    canonical_recovered, canonical_cursor = _pull_canonical(db, client, state, after=canonical_after, limit=limit)
    return {
        'linked': True,
        'recovered': snapshot_rows + history_recovered + canonical_recovered,
        'cursor': history_cursor or canonical_cursor or history_after or canonical_after,
        'history_recovered': history_recovered,
        'canonical_recovered': canonical_recovered,
        'history_cursor': history_cursor or history_after,
        'canonical_cursor': canonical_cursor or canonical_after,
        'snapshot_restored': bool(snapshot),
        'snapshot_rows': snapshot_rows,
        'snapshot_id': snapshot.get('snapshot_id', '') if snapshot else '',
    }