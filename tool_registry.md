# Tool Registry

Version: v1.0
Date: 2026-04-16

## Cloud Sync
- `kontext_cloud_status`: shows whether this database is linked, plus the active workspace, device, and cursors.
- `kontext_cloud_link`: enrolls the current database into a cloud workspace and stores the local link state.
- `kontext_cloud_sync`: pushes local history and canonical revisions, then pulls the remote tail.
- `kontext_cloud_recover`: restores from the latest server snapshot when available, then replays any newer history and canonical revisions.

## Cloud Control Plane
- `POST /v1/snapshots/create`: captures a server-side workspace snapshot for fresh-device bootstrap and recovery.
- `GET /v1/snapshots/latest`: returns the latest snapshot payload for an enrolled device.