// Kontext — mock memory library data
// Shapes mirror the real SQLite schema described in the brief.

window.KONTEXT_DATA = (() => {
  const now = new Date('2026-04-18T14:23:07Z').getTime();
  const H = 3600 * 1000, D = 24 * H;

  const entries = [
    { id: 'e001', file: 'user_identity.md',            type: 'user',      tier: 'S', desc: 'Core identity, pronouns, timezone, working hours', decay: 0.02, lastUsed: now - 2*H,    created: now - 180*D, uses: 412, relations: ['e004','e007','e012'], why: 'Referenced in 89% of sessions; load-bearing for tone calibration.', body: 'Ionut. He/him. Bucharest (Europe/Bucharest, UTC+3). Deep work 09:00–13:00 local. Hard stop Fridays 17:00. Prefers terse responses, no preamble. Dislikes corporate hedging. Engineering background — C/Rust/Python. Runs Kontext as a nights-and-weekends project.' },
    { id: 'e002', file: 'kontext_architecture.md',     type: 'project',   tier: 'S', desc: 'Stack, storage model, sync layer',                decay: 0.05, lastUsed: now - 4*H,    created: now - 142*D, uses: 287, relations: ['e003','e008','e015'], why: 'Loaded on any Kontext-related prompt; canonical reference.', body: 'Flask + Jinja2 + Tailwind (CDN) + HTMX 1.9 + Alpine 3.14. SQLite with FTS5 + sqlite-vec for semantic. Cloud sync over LiteFS. Hooks auto-capture from Claude Code sessions into ~/.kontext/workspaces/<slug>/entries/.' },
    { id: 'e003', file: 'sqlite_fts5_notes.md',        type: 'reference', tier: 'A', desc: 'FTS5 tokenizer config and ranking tuning',        decay: 0.12, lastUsed: now - 18*H,   created: now - 96*D,  uses: 94,  relations: ['e002'],                  why: 'Cited when tuning search; technical load-bearing.', body: 'porter unicode61 remove_diacritics 2. BM25(k1=1.2, b=0.75). Prefix indexing on triples. Rebuild idx on write; 12ms p99 @ 8k rows.' },
    { id: 'e004', file: 'communication_prefs.md',      type: 'user',      tier: 'S', desc: 'Tone, format, what to skip',                      decay: 0.01, lastUsed: now - 30*60*1000, created: now - 210*D, uses: 612, relations: ['e001'],                why: 'Global tone filter; applied to every response.', body: 'No throat-clearing. Code blocks only when asked. Bullets over prose when enumerating. One question at a time. Admit uncertainty early, once.' },
    { id: 'e005', file: 'feedback_2026_03.md',         type: 'feedback',  tier: 'A', desc: 'Response too verbose on simple lookups',          decay: 0.18, lastUsed: now - 6*D,    created: now - 38*D,  uses: 22,  relations: ['e004'],                  why: 'Correction on tone; feeds into future calibration.', body: 'Flagged 2026-03-11: responses to "what\'s the X" queries returned 3+ paragraphs. Wants <1 sentence when the question is factual.' },
    { id: 'e006', file: 'decay_scoring_spec.md',       type: 'project',   tier: 'A', desc: 'Algorithm for entry relevance decay',             decay: 0.08, lastUsed: now - 11*H,   created: now - 62*D,  uses: 71,  relations: ['e002','e015'],           why: 'Referenced when reviewing library health.', body: 'decay = 1 - exp(-lambda * days_since_used) * (uses + 1)^-0.3. lambda per-tier: S=0.004, A=0.008, B=0.02, C=0.05.' },
    { id: 'e007', file: 'project_index.md',            type: 'project',   tier: 'S', desc: 'Active projects, one-line summaries, stage',      decay: 0.04, lastUsed: now - 3*H,    created: now - 155*D, uses: 331, relations: ['e001','e008','e009'],    why: 'Orientation doc; loaded at session start.', body: '• Kontext — memory library, alpha, 3 users\n• Lanterna — iOS reading app, sunset\n• graphd — graph DB experiment, paused\n• dotfiles-2026 — config migration, active' },
    { id: 'e008', file: 'kontext_roadmap.md',          type: 'project',   tier: 'A', desc: 'Q2 milestones, deferred items',                   decay: 0.15, lastUsed: now - 2*D,    created: now - 48*D,  uses: 45,  relations: ['e002','e007'],           why: 'Planning context; decays unless actively referenced.', body: 'Q2: multi-user invite flow, decay UI, public read-only pages. Deferred: mobile viewer, GraphQL API, webhook export.' },
    { id: 'e009', file: 'dotfiles_migration.md',       type: 'project',   tier: 'B', desc: 'nvim/zsh/tmux config port to 2026 setup',         decay: 0.22, lastUsed: now - 4*D,    created: now - 28*D,  uses: 18,  relations: ['e007'],                  why: 'Active project state; keep until migration closes.', body: 'nvim 0.11 lazy.nvim spec drafted. zsh zinit→sheldon. tmux sessionizer fzf picker in place.' },
    { id: 'e010', file: 'writing_voice.md',            type: 'user',      tier: 'A', desc: 'Blog/thread voice, phrases to avoid',             decay: 0.09, lastUsed: now - 22*H,   created: now - 112*D, uses: 88,  relations: ['e001','e004'],           why: 'Applied to any drafting task.', body: 'Short sentences. No "delve", "leverage", "robust". Em-dash OK. Lowercase starts in notes are fine. Never end with "hope this helps".' },
    { id: 'e011', file: 'reading_list_2026.md',        type: 'reference', tier: 'B', desc: 'Books queued, read, dropped',                     decay: 0.31, lastUsed: now - 8*D,    created: now - 88*D,  uses: 14,  relations: [],                        why: 'Occasional retrieval; low-frequency OK.', body: 'Queued: The Information (Gleick), Seeing Like a State. Read: Ultra-Processed People. Dropped: Fourth Wing.' },
    { id: 'e012', file: 'health_constraints.md',       type: 'user',      tier: 'S', desc: 'Sleep window, caffeine cutoff, RSI',              decay: 0.03, lastUsed: now - 1*D,    created: now - 134*D, uses: 156, relations: ['e001'],                  why: 'Context for scheduling, advice calibration.', body: 'Sleep 23:30–07:00. No caffeine after 14:00. Right-hand RSI — long coding sessions flagged at 90min.' },
    { id: 'e013', file: 'feedback_2026_04_02.md',      type: 'feedback',  tier: 'B', desc: 'Wanted fewer bullet points in threads',           decay: 0.26, lastUsed: now - 12*D,   created: now - 16*D,  uses: 4,   relations: ['e010'],                  why: 'Minor correction; may fold into writing_voice.', body: 'Flagged 2026-04-02: too many nested bullets in a Twitter thread draft. Prefer paragraphs of 2–3 sentences.' },
    { id: 'e014', file: 'api_keys_index.md',           type: 'reference', tier: 'A', desc: 'Pointer to 1Password vault items, not secrets',   decay: 0.11, lastUsed: now - 16*H,   created: now - 71*D,  uses: 62,  relations: [],                        why: 'Directory only; never stores actual keys.', body: 'Claude API — 1P://Private/claude-api. Fly.io — 1P://Kontext/fly-token. GitHub — 1P://Private/gh-pat.' },
    { id: 'e015', file: 'sync_protocol.md',            type: 'project',   tier: 'A', desc: 'LiteFS replication, conflict rules',              decay: 0.14, lastUsed: now - 20*H,   created: now - 54*D,  uses: 58,  relations: ['e002','e006'],           why: 'Technical doc; loaded on sync-related work.', body: 'Primary = laptop. Replicas = desktop, phone (read-only). Conflicts: last-writer-wins at entry grain. Tombstones 30d.' },
    { id: 'e016', file: 'decisions_log.md',            type: 'project',   tier: 'A', desc: 'ADR-style record of architecture decisions',      decay: 0.07, lastUsed: now - 9*H,    created: now - 148*D, uses: 103, relations: ['e002','e008'],           why: 'Decision context; keep warm.', body: '2026-02-14 — chose SQLite over Postgres (single-user primary). 2026-03-01 — dropped Redis (not needed at scale). 2026-04-05 — HTMX over SPA (simpler hot path).' },
    { id: 'e017', file: 'travel_2026.md',              type: 'reference', tier: 'C', desc: 'Flights, hotels, loose plans',                    decay: 0.58, lastUsed: now - 34*D,   created: now - 40*D,  uses: 3,   relations: [],                        why: 'Low-value long-tail; candidate for C→dropped.', body: 'Lisbon 2026-05 (booked). Maybe Tokyo autumn.' },
    { id: 'e018', file: 'food_preferences.md',         type: 'user',      tier: 'B', desc: 'Diet constraints, restaurants loved',             decay: 0.19, lastUsed: now - 5*D,    created: now - 99*D,  uses: 31,  relations: ['e001'],                  why: 'Useful for scheduling, travel, gifting context.', body: 'Vegetarian. No mushrooms. Strong coffee fan. Bucharest: M60, Origo, Beans & Dots.' },
    { id: 'e019', file: 'git_conventions.md',          type: 'user',      tier: 'A', desc: 'Commit message format, branch naming',            decay: 0.10, lastUsed: now - 7*H,    created: now - 124*D, uses: 142, relations: ['e004'],                  why: 'Applied whenever generating commits.', body: 'Conventional commits. Lowercase subject, no period. Branches: <type>/<slug>. No emoji.' },
    { id: 'e020', file: 'feedback_2026_04_10.md',      type: 'feedback',  tier: 'A', desc: 'Hallucinated a package name',                     decay: 0.17, lastUsed: now - 3*D,    created: now - 8*D,   uses: 7,   relations: ['e003'],                  why: 'Accuracy correction; keeps pressure on verification.', body: 'Flagged 2026-04-10: suggested `sqlite-vectra` which does not exist. Correct: `sqlite-vec`. Watch for plausible-sounding package names.' },
    { id: 'e021', file: 'legacy_evernote.md',          type: 'reference', tier: 'C', desc: 'Imported notes from Evernote 2019–2022',          decay: 0.71, lastUsed: now - 58*D,   created: now - 160*D, uses: 2,   relations: [],                        why: 'Archival; unlikely retrieval.', body: '~2400 notes imported. Search-only, no auto-load.' },
    { id: 'e022', file: 'graphd_notes.md',             type: 'project',   tier: 'C', desc: 'Paused graph DB experiment',                      decay: 0.64, lastUsed: now - 42*D,   created: now - 78*D,  uses: 8,   relations: ['e007'],                  why: 'Project paused; decaying naturally.', body: 'Cayley fork idea. Paused pending Kontext alpha.' },
    { id: 'e023', file: 'feedback_2026_01.md',         type: 'feedback',  tier: 'B', desc: 'Too aggressive with apologies',                   decay: 0.24, lastUsed: now - 22*D,   created: now - 84*D,  uses: 11,  relations: ['e004'],                  why: 'Older correction; merging into comm_prefs.', body: 'Flagged 2026-01-19: too many "Apologies for the confusion" openings.' },
    { id: 'e024', file: 'music_taste.md',              type: 'user',      tier: 'B', desc: 'Work music, no-go genres',                        decay: 0.20, lastUsed: now - 6*D,    created: now - 102*D, uses: 26,  relations: ['e001'],                  why: 'Used for playlist context.', body: 'Work: ambient, Brian Eno, Hania Rani, Nils Frahm. No lyrics during deep work.' },
    { id: 'e025', file: 'device_laptop.md',            type: 'reference', tier: 'A', desc: 'MBP M3 Max specs + dotfiles link',                decay: 0.09, lastUsed: now - 14*H,   created: now - 70*D,  uses: 51,  relations: ['e009'],                  why: 'Device context for env-specific help.', body: 'MBP 16" M3 Max 64GB. macOS 15.4. Primary dev machine.' },
    { id: 'e026', file: 'device_desktop.md',           type: 'reference', tier: 'B', desc: 'Linux desktop, dual monitor',                     decay: 0.21, lastUsed: now - 4*D,    created: now - 66*D,  uses: 19,  relations: ['e009'],                  why: 'Secondary machine; batch/render workloads.', body: 'Ryzen 9 7950X, 64GB, 2× 27" 4K. Arch. Used for renders + long builds.' },
    { id: 'e027', file: 'people_close.md',             type: 'user',      tier: 'A', desc: 'Close relationships, context',                    decay: 0.13, lastUsed: now - 2*D,    created: now - 128*D, uses: 67,  relations: ['e001'],                  why: 'Personal context; handle with care.', body: 'Partner: Maria. Brother: Andrei (CS student). Close collaborators on Kontext: @tomas, @lin.' },
    { id: 'e028', file: 'errors_taxonomy.md',          type: 'reference', tier: 'B', desc: 'Common error classes in my code',                 decay: 0.23, lastUsed: now - 5*D,    created: now - 52*D,  uses: 17,  relations: ['e002'],                  why: 'Debugging shortcut; speeds triage.', body: 'Async context leaks. Timezone drift (always Europe/Bucharest). Off-by-one in FTS ranking.' },
    { id: 'e029', file: 'feedback_2026_02.md',         type: 'feedback',  tier: 'C', desc: 'Used a metaphor I disliked',                      decay: 0.45, lastUsed: now - 32*D,   created: now - 60*D,  uses: 3,   relations: ['e010'],                  why: 'Minor stylistic correction; low weight.', body: 'Flagged 2026-02-08: over-use of "at the end of the day".' },
    { id: 'e030', file: 'lanterna_postmortem.md',      type: 'project',   tier: 'C', desc: 'Sunset reading app lessons',                      decay: 0.54, lastUsed: now - 28*D,   created: now - 175*D, uses: 9,   relations: ['e007'],                  why: 'Archival reference for future app work.', body: 'Lanterna sunset 2026-01. Lessons: don\'t rely on iCloud sync; pick one platform; ship core loop before polish.' },
  ];

  const devices = [
    { id: 'mbp',     label: 'MBP 16\" M3 Max',     role: 'primary',  last: now - 4*60*1000,     captures24h: 47, status: 'online' },
    { id: 'linux',   label: 'Ryzen desktop',       role: 'replica',  last: now - 2*H,           captures24h: 3,  status: 'idle' },
    { id: 'phone',   label: 'iPhone 15 Pro',       role: 'readonly', last: now - 38*60*1000,    captures24h: 0,  status: 'online' },
  ];

  // 14-day daily history of score dimensions
  const history = [];
  const dims = { breadth: 72, depth: 78, recency: 88, longevity: 84, linkage: 68 };
  for (let d = 13; d >= 0; d--) {
    const t = now - d*D;
    history.push({
      t,
      breadth: Math.round(dims.breadth + Math.sin(d*0.4)*3 - d*0.15),
      depth: Math.round(dims.depth + Math.cos(d*0.3)*2 - d*0.1),
      recency: Math.round(dims.recency - Math.sin(d*0.5)*4 + d*0.2),
      longevity: Math.round(dims.longevity + Math.cos(d*0.2)*1.5),
      linkage: Math.round(dims.linkage + Math.sin(d*0.6)*3 - d*0.3),
      captures: Math.max(0, Math.round(32 + Math.sin(d*0.7)*18 + (Math.random()*8-4))),
      prompts: Math.max(0, Math.round(84 + Math.cos(d*0.4)*22)),
    });
  }

  // Score = weighted average
  const current = history[history.length - 1];
  const prev    = history[history.length - 8]; // 7 days ago
  const score = Math.round((current.breadth*0.18 + current.depth*0.22 + current.recency*0.25 + current.longevity*0.15 + current.linkage*0.20));
  const prevScore = Math.round((prev.breadth*0.18 + prev.depth*0.22 + prev.recency*0.25 + prev.longevity*0.15 + prev.linkage*0.20));

  // Live capture feed (last 24h-ish)
  const feed = [
    { t: now - 4*60*1000,     ev: 'capture', file: 'kontext_roadmap.md',      action: 'appended', bytes: 142, device: 'mbp',   source: 'claude-code:edit' },
    { t: now - 11*60*1000,    ev: 'link',    file: 'user_identity.md',         action: 'linked → communication_prefs.md', device: 'mbp',   source: 'hook:relations' },
    { t: now - 38*60*1000,    ev: 'capture', file: 'feedback_2026_04_10.md',   action: 'created',  bytes: 287, device: 'phone', source: 'inbox:apple-mail' },
    { t: now - 1*H,           ev: 'promote', file: 'git_conventions.md',        action: 'B → A (tier)', device: 'mbp', source: 'auto:usage-threshold' },
    { t: now - 2*H,            ev: 'capture', file: 'decisions_log.md',          action: 'appended', bytes: 94,  device: 'mbp',   source: 'claude-code:session-end' },
    { t: now - 4*H,            ev: 'decay',   file: 'travel_2026.md',            action: 'decay 0.52 → 0.58', device: 'mbp', source: 'cron:daily' },
    { t: now - 6*H,            ev: 'capture', file: 'dotfiles_migration.md',     action: 'appended', bytes: 312, device: 'mbp',   source: 'claude-code:edit' },
    { t: now - 9*H,            ev: 'sync',    file: '—',                          action: '14 entries → desktop', device: 'linux', source: 'litefs:replicate' },
    { t: now - 14*H,           ev: 'capture', file: 'errors_taxonomy.md',        action: 'edited',   bytes: 58,  device: 'mbp',   source: 'claude-code:edit' },
  ];

  return {
    now, entries, devices, history, feed,
    score, prevScore,
    dimensions: current,
    prevDimensions: prev,
    totals: {
      entries: entries.length * 3 + 7,  // pretend there are more
      devices: devices.length,
      histOps: 14823,
      canonical: entries.filter(e => e.tier === 'S' || e.tier === 'A').length,
    },
    activity24h: {
      toolEvents: 312,
      prompts: current.prompts,
      entriesTouched: 18,
      lastCaptureAgo: 4 * 60 * 1000,
    },
  };
})();
