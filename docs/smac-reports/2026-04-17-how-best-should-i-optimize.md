# SMAC Report: How best should I optimize this codebase?

**Generated:** 2026-04-17 | **Agents:** 5R + 3V | **Overall confidence:** 92%

> Warning: LOW_VERIFICATION_COVERAGE — only 1 verifier returned schema-valid output. Main-thread quote checks were applied to every finding before ranking.

## Ranked Findings

| # | Finding | Impact | Effort | Confidence | Verified | Score |
|---|---|---|---|---|---|---|
| 1 | Digest dedup does a repo-wide fuzzy scan | HIGH | MED | 96% | CONFIRMED | 2.88 |
| 2 | WhatsApp intake rereads `.txt` exports | MED | LOW | 94% | CONFIRMED | 1.88 |
| 3 | Chunk overlap repeats whole conversations | MED | LOW | 83% | CONFIRMED | 1.66 |
| 4 | History replay pays one transaction per envelope | MED | MED | 90% | UNVERIFIED | 0.72 |
| 5 | Pulled pages re-upsert the same source device repeatedly | LOW | LOW | 88% | UNVERIFIED | 0.35 |

---

## Finding 1: Digest dedup does a repo-wide fuzzy scan

**Researchers:** Digest and Intake Pipeline
**Cross-verified by:** Digest and Intake Pipeline verifier — CONFIRMED
**Impact:** HIGH | **Effort:** MED | **Confidence:** 96%

### Evidence
- `digest.py:350` — `SequenceMatcher(None, text_lower, f).ratio() >= 0.85`
- Supporting context: `digest.py:342-350` loads all stored facts first, and `digest.py:540-555` runs a second fuzzy pass over candidates.

### Description
The default digest path materializes the full `entries` fact set and then performs another fuzzy comparison pass across the candidate list, so dedup cost scales with both corpus size and run size.

### Recommendation
Bound fuzzy matching by source file, grade, or a cheaper indexed prefix or hash bucket before falling back to full-string similarity.

---

## Finding 2: WhatsApp intake rereads `.txt` exports

**Researchers:** Digest and Intake Pipeline
**Cross-verified by:** Digest and Intake Pipeline verifier — CONFIRMED
**Impact:** MED | **Effort:** LOW | **Confidence:** 94%

### Evidence
- `pipeline/parsers.py:303` — `with open(path, "r", encoding="utf-8") as f:`
- Supporting context: `pipeline/parsers.py:503-516` reads the whole `.txt` file into `text` for detection before `parse_whatsapp_file()` opens it again.

### Description
The `.txt` detection branch reads the full export once for sniffing and then the WhatsApp parser reopens the same file for the actual parse.

### Recommendation
Pass the already-read buffer into the WhatsApp parser or split detection from parsing so the file is only opened once.

---

## Finding 3: Chunk overlap repeats whole conversations

**Researchers:** Digest and Intake Pipeline
**Cross-verified by:** Digest and Intake Pipeline verifier — CONFIRMED
**Impact:** MED | **Effort:** LOW | **Confidence:** 83%

### Evidence
- `pipeline/chunker.py:192` — `current_convos = [last_convo]`
- Supporting context: `pipeline/chunker.py:8-10` says chunks use a 2,000-token overlap, but `pipeline/chunker.py:188-193` carries the entire last conversation forward when it fits.

### Description
Adjacent chunks can repeat a whole prior conversation instead of only a bounded tail window, which reprocesses more text than the overlap contract suggests.

### Recommendation
Use a bounded tail window or message-level overlap so adjacent chunks repeat only the minimum continuity needed.

---

## Finding 4: History replay pays one transaction per envelope

**Researchers:** Cloud Sync and Control Plane
**Cross-verified by:** none — UNVERIFIED
**Impact:** MED | **Effort:** MED | **Confidence:** 90%

### Evidence
- `cloud/replay.py:82` — `with db.transaction():`
- Supporting context: `cloud/api.py:194-205` and `cloud/daemon.py:138-152` feed `apply_history_op` one item at a time.

### Description
Replay work is wrapped in one transaction per envelope instead of one transaction per pulled page, which adds transaction overhead as sync batches grow.

### Recommendation
Add a batch replay helper that opens one transaction per page and applies all envelopes before advancing the cursor.

---

## Finding 5: Pulled pages re-upsert the same source device repeatedly

**Researchers:** Cloud Sync and Control Plane
**Cross-verified by:** none — UNVERIFIED
**Impact:** LOW | **Effort:** LOW | **Confidence:** 88%

### Evidence
- `cloud/daemon.py:114` — `db.register_device(`
- Supporting context: `cloud/daemon.py:138-152` calls `_sync_source_device` for every pulled row, and that helper immediately upserts the source device.

### Description
Pages with many rows from the same remote device repeat the same local device upsert instead of registering that device once per page.

### Recommendation
Collect unique `device_id`s per page and register each source device once before replaying the items.

---

## Disputed Findings

| Finding | Researcher | Verifier | Dispute Reason |
|---|---|---|---|
| Sync still pays full fuzzy-dedup cost on unchanged files | Storage and Local Sync | none | QUOTE_MISMATCH |
| Dream-triggered export rewrites the entire memory tree | Storage and Local Sync | none | QUOTE_MISMATCH |
| add_entry does an extra lookup on every sync import | Storage and Local Sync | none | QUOTE_MISMATCH |
| Pull API does a per-row source-device lookup | Cloud Sync and Control Plane | Cloud verifier | QUOTE_MISMATCH |

## Coverage Gaps

| Role | Status | Impact |
|---|---|---|
| MCP Retrieval and Tool Wiring | parse_fail researcher schema | Findings excluded before synthesis |
| UI Hooks and Local Startup | parse_fail researcher schema | Findings excluded before synthesis |
| Cloud Sync and Control Plane verifier | parse_fail verifier schema | Cloud findings downgraded to UNVERIFIED |
| Storage and Local Sync verifier | parse_fail verifier schema | Storage findings relied on main-thread quote check only |
