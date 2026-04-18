#!/bin/bash
# Kontext client-side sync. Runs on a client machine (PC, server container,
# etc.) that holds a local copy of the memory library synced to the central
# Kontext cloud server.
#
# 3 phases:
#   1. Seed push cursor on first run (prevents re-shoveling the entire
#      pulled history — history_ops ids are UUIDs, not insertion-ordered,
#      so a naive cursor-less push would 403 on any revoked co-tenant).
#   2. Scoped push: only ops authored by THIS device.
#   3. Pull history + canonical, then export every file as .md so the
#      auto-routing hooks pick up fresh content.
#
# Prereqs:
#   - /root/kontext-mcp/ checked out on the host (bind-mounted into the
#     target container if running inside one).
#   - Device enrolled against the cloud server; workspace state file at
#     /root/.kontext/server.cloud.json and keystore at /root/.kontext/keys.
#   - Memory dir target: /root/.kontext/memory/ (created on first run).
#
# Install (cron, VPS host, target is a bind-mounted ttyd-claude container):
#   chmod +x /root/.kontext/_sync.sh
#   (crontab -l; echo '*/2 * * * * docker exec <container> bash /root/.kontext/_sync.sh >> /root/.kontext/_sync.log 2>&1') | crontab -
#
# Install (cron, bare host):
#   (crontab -l; echo '*/2 * * * * /root/.kontext/_sync.sh >> /root/.kontext/_sync.log 2>&1') | crontab -
set -eo pipefail
cd /root/kontext-mcp
export PYTHONPATH=/root/kontext-mcp
exec python3 - <<'PY'
import sys, json, time
from pathlib import Path
sys.path.insert(0, '/root/kontext-mcp')
from db import KontextDB
from cloud.daemon import (
    _require_state, _authed_client, _current_cursor,
    _pull_history, _pull_canonical, _history_payload_item,
)
from export import export_all, export_memory_index

t0 = time.time()
db = KontextDB('/root/.kontext/server.db')
state = _require_state(db)
# Pass explicit keystore_root — default resolution doesn't point at
# /root/.kontext/keys/ in all container layouts (notably linuxserver.io
# code-server, where HOME=/config), which would produce a 401 no-token.
client = _authed_client(state, keystore_root=Path('/root/.kontext/keys'))
ws, dev = state['workspace_id'], state['device_id']

# Phase 1: self-seed push cursor on first run so we never attempt to
# re-push everything we've pulled. No-op on subsequent runs.
if not _current_cursor(db, ws, dev, 'history_push'):
    mx = db.conn.execute("SELECT MAX(id) FROM history_ops").fetchone()[0]
    if mx:
        db.advance_sync_cursor(ws, dev, 'history_push', mx)

# Phase 2: scoped push — only ops THIS device created.
push_after = _current_cursor(db, ws, dev, 'history_push')
rows = db.list_history_ops_since(ws, push_after, limit=500)
mine = [r for r in rows if r['device_id'] == dev]
pushed = 0
if mine:
    items = [_history_payload_item(r) for r in mine]
    client.push_history(ws, items)
    pushed = len(items)
if rows:
    db.advance_sync_cursor(ws, dev, 'history_push', rows[-1]['id'])

# Phase 3: pull from cloud.
h_pulled, _ = _pull_history(db, client, state,
    after=_current_cursor(db, ws, dev, 'history'), limit=500)
c_pulled, _ = _pull_canonical(db, client, state,
    after=_current_cursor(db, ws, dev, 'canonical'), limit=500)

# Export DB -> .md for the router.
outdir = Path('/root/.kontext/memory')
outdir.mkdir(parents=True, exist_ok=True)
export_all(db, outdir)
export_memory_index(db, outdir)

print(json.dumps({
    'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'elapsed_ms': int((time.time() - t0) * 1000),
    'pushed': pushed,
    'pulled_history': h_pulled,
    'pulled_canonical': c_pulled,
    'md_files': len(list(outdir.glob('*.md'))),
}))
PY
