// Entries page — three-pane: filters+stats / list / detail
// (Merged from former "Top" page — library aggregates now live in the left rail.)

function Entries({ initialId }) {
  const D = window.KONTEXT_DATA;
  const [q, setQ] = useState('');
  const [filters, setFilters] = useState({ type: new Set(), tier: new Set() });
  const [selected, setSelected] = useState(initialId || D.entries[0].id);
  const [bulkMode, setBulkMode] = useState(false);
  const [picked, setPicked] = useState(new Set());
  const [sort, setSort] = useState('recent'); // recent | top | decay
  const listRef = useRef(null);

  const topRanked = useMemo(
    () => [...D.entries].sort((a, b) => b.uses - a.uses).slice(0, 5),
    []
  );

  const filtered = useMemo(() => {
    const qq = q.trim().toLowerCase();
    const arr = D.entries.filter(e => {
      if (filters.type.size && !filters.type.has(e.type)) return false;
      if (filters.tier.size && !filters.tier.has(e.tier)) return false;
      if (qq) {
        const hay = (e.file + ' ' + e.desc + ' ' + e.body).toLowerCase();
        if (!hay.includes(qq)) return false;
      }
      return true;
    });
    if (sort === 'top')   arr.sort((a, b) => b.uses - a.uses);
    if (sort === 'decay') arr.sort((a, b) => b.decay - a.decay);
    if (sort === 'recent') arr.sort((a, b) => b.lastUsed - a.lastUsed);
    return arr;
  }, [q, filters, sort]);

  const selectedIdx = filtered.findIndex(e => e.id === selected);
  const entry = D.entries.find(e => e.id === selected);

  // j/k navigation
  useKey(['j','k'], (e) => {
    if (e.key === 'j' && selectedIdx < filtered.length - 1) {
      setSelected(filtered[selectedIdx + 1].id);
    } else if (e.key === 'k' && selectedIdx > 0) {
      setSelected(filtered[selectedIdx - 1].id);
    }
    e.preventDefault();
  }, [selectedIdx, filtered]);

  useKey(['x'], () => {
    if (!bulkMode) return;
    setPicked(prev => {
      const next = new Set(prev);
      if (next.has(selected)) next.delete(selected); else next.add(selected);
      return next;
    });
  }, [selected, bulkMode]);

  useKey(['/'], (e) => {
    e.preventDefault();
    const inp = document.getElementById('entries-search');
    if (inp) inp.focus();
  }, []);

  // Scroll selected into view
  useEffect(() => {
    const row = listRef.current?.querySelector(`[data-id="${selected}"]`);
    if (row && row.scrollIntoViewIfNeeded) row.scrollIntoViewIfNeeded();
    else if (row) {
      const rect = row.getBoundingClientRect();
      const parent = listRef.current.getBoundingClientRect();
      if (rect.top < parent.top || rect.bottom > parent.bottom) {
        listRef.current.scrollTop += rect.top - parent.top - 80;
      }
    }
  }, [selected]);

  const typeCounts = useMemo(() => {
    const c = {};
    D.entries.forEach(e => c[e.type] = (c[e.type]||0) + 1);
    return c;
  }, []);
  const tierCounts = useMemo(() => {
    const c = {};
    D.entries.forEach(e => c[e.tier] = (c[e.tier]||0) + 1);
    return c;
  }, []);

  const toggle = (bucket, val) => {
    setFilters(prev => {
      const next = { ...prev, [bucket]: new Set(prev[bucket]) };
      if (next[bucket].has(val)) next[bucket].delete(val); else next[bucket].add(val);
      return next;
    });
  };

  return (
    <div className="split-3 mount">
      {/* PANE 1 — filters + library stats */}
      <aside style={{ borderRight: '1px solid var(--hair)', display: 'flex', flexDirection: 'column', overflow: 'auto' }}>
        {/* Totals strip */}
        <div style={{ padding: '14px 14px 12px', borderBottom: '1px solid var(--hair)' }}>
          <div className="lbl" style={{ marginBottom: 10 }}>Library</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 14px' }}>
            {[
              { k: 'entries',   v: D.totals.entries },
              { k: 'canonical', v: D.totals.canonical },
              { k: 'devices',   v: D.totals.devices },
              { k: 'hist ops',  v: D.totals.histOps },
            ].map(x => (
              <div key={x.k}>
                <div className="lbl lbl-sm" style={{ marginBottom: 2 }}>{x.k}</div>
                <div style={{ fontFamily: 'var(--f-mono)', fontSize: 18, color: 'var(--hi)', letterSpacing: '-0.02em' }}>
                  {fmtNum(x.v)}
                </div>
              </div>
            ))}
          </div>
        </div>

        <header style={{ padding: '10px 14px', borderBottom: '1px solid var(--hair)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span className="lbl">Filter</span>
          {(filters.type.size || filters.tier.size) ? (
            <button className="btn ghost" style={{ padding: '2px 6px', fontSize: 11, color: 'var(--dim)' }} onClick={() => setFilters({ type: new Set(), tier: new Set() })}>clear</button>
          ) : null}
        </header>

        <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--hair)' }}>
          <div className="lbl" style={{ marginBottom: 8 }}>Type</div>
          {Object.entries(typeCounts).map(([k, n]) => (
            <label key={k} style={{ display: 'grid', gridTemplateColumns: '12px 1fr auto', gap: 8, alignItems: 'center', padding: '3px 0', cursor: 'pointer' }}>
              <span style={{ width: 10, height: 10, border: '1px solid var(--hair-hi)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: filters.type.has(k) ? 'var(--fg)' : 'var(--bg)' }}>
                {filters.type.has(k) && <svg width="8" height="8" viewBox="0 0 8 8"><path d="M1 4 L3 6 L7 2" fill="none" stroke="var(--bg)" strokeWidth="1.5" /></svg>}
              </span>
              <span style={{ fontSize: 12, color: filters.type.has(k) ? 'var(--hi)' : 'var(--fg)' }} onClick={() => toggle('type', k)}>{k}</span>
              <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>{n}</span>
            </label>
          ))}
        </div>

        <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--hair)' }}>
          <div className="lbl" style={{ marginBottom: 8 }}>Tier</div>
          {['S','A','B','C'].map(k => (
            <label key={k} style={{ display: 'grid', gridTemplateColumns: '12px 24px 1fr auto', gap: 8, alignItems: 'center', padding: '3px 0', cursor: 'pointer' }}>
              <span style={{ width: 10, height: 10, border: '1px solid var(--hair-hi)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: filters.tier.has(k) ? 'var(--fg)' : 'var(--bg)' }} onClick={() => toggle('tier', k)}>
                {filters.tier.has(k) && <svg width="8" height="8" viewBox="0 0 8 8"><path d="M1 4 L3 6 L7 2" fill="none" stroke="var(--bg)" strokeWidth="1.5" /></svg>}
              </span>
              <span className={`tier ${k}`}>{k}</span>
              <span style={{ fontSize: 11, color: 'var(--dim)' }}>
                {{'S':'load-bearing','A':'canonical','B':'active','C':'archival'}[k]}
              </span>
              <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>{tierCounts[k] || 0}</span>
            </label>
          ))}
        </div>

        <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--hair)' }}>
          <div className="lbl" style={{ marginBottom: 8 }}>Saved</div>
          {[
            { n: 'Load-bearing (S)', q: 's' },
            { n: 'Decay > 0.5',      q: 'decay' },
            { n: 'No relations',     q: 'orphan' },
            { n: 'Feedback · 30d',   q: 'fb' },
          ].map(s => (
            <div key={s.n} style={{ fontSize: 12, color: 'var(--dim)', padding: '3px 0', cursor: 'pointer' }}>
              <span style={{ fontFamily: 'var(--f-mono)', color: 'var(--dim)', marginRight: 8 }}>·</span>{s.n}
            </div>
          ))}
        </div>

        {/* Top entries — 7d (from former Top page) */}
        <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--hair)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
            <span className="lbl">Top \u00b7 7d</span>
            <button className="btn ghost" onClick={() => setSort('top')} style={{ fontSize: 10, color: 'var(--dim)', padding: '1px 4px' }}>see all \u2192</button>
          </div>
          {topRanked.map((e, i) => (
            <div key={e.id} onClick={() => setSelected(e.id)} style={{
              display: 'grid', gridTemplateColumns: '18px 16px 1fr 28px', gap: 6,
              padding: '4px 0', alignItems: 'center', cursor: 'pointer',
            }}>
              <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>{String(i+1).padStart(2,'0')}</span>
              <span className={`tier ${e.tier}`} style={{ width: 14, height: 14, fontSize: 9 }}>{e.tier}</span>
              <span style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--hi)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.file}</span>
              <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--fg)', textAlign: 'right' }}>{e.uses}</span>
            </div>
          ))}
        </div>

        <div style={{ padding: '12px 14px', marginTop: 'auto', borderTop: '1px solid var(--hair)' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--fg)', cursor: 'pointer' }}>
            <span style={{ width: 10, height: 10, border: '1px solid var(--hair-hi)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: bulkMode ? 'var(--accent)' : 'var(--bg)' }}>
              {bulkMode && <svg width="8" height="8" viewBox="0 0 8 8"><path d="M1 4 L3 6 L7 2" fill="none" stroke="var(--bg)" strokeWidth="1.5" /></svg>}
            </span>
            <span onClick={() => setBulkMode(!bulkMode)}>Bulk tier review</span>
            <span className="kbd" style={{ marginLeft: 'auto' }}>B</span>
          </label>
          {bulkMode && <div style={{ fontSize: 10, fontFamily: 'var(--f-mono)', color: 'var(--dim)', marginTop: 8 }}>
            X toggle · 1–4 assign tier · ⏎ commit
          </div>}
        </div>
      </aside>

      {/* PANE 2 — list */}
      <main style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <header style={{ padding: '8px 14px', borderBottom: '1px solid var(--hair)', display: 'flex', gap: 10, alignItems: 'center' }}>
          <div className="input" style={{ flex: 1, padding: '4px 10px' }}>
            <Icon d={Icons.search} />
            <input id="entries-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Keyword + semantic search ·  / to focus" />
            {q && <button onClick={() => setQ('')} style={{ color: 'var(--dim)' }}><Icon d={Icons.close} size={10} /></button>}
          </div>
          <div style={{ display: 'flex', border: '1px solid var(--hair)' }}>
            {[
              { k: 'recent', l: 'recent' },
              { k: 'top',    l: 'top' },
              { k: 'decay',  l: 'decay' },
            ].map((s, i) => (
              <button key={s.k} onClick={() => setSort(s.k)}
                style={{
                  padding: '3px 9px',
                  fontFamily: 'var(--f-mono)', fontSize: 10,
                  color: sort === s.k ? 'var(--hi)' : 'var(--dim)',
                  background: sort === s.k ? 'var(--surface-2)' : 'transparent',
                  borderLeft: i > 0 ? '1px solid var(--hair)' : 'none',
                  textTransform: 'uppercase', letterSpacing: '0.08em',
                }}>{s.l}</button>
            ))}
          </div>
          <div style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--dim)' }}>
            <span style={{ color: 'var(--hi)' }}>{filtered.length}</span> / {D.entries.length}
          </div>
        </header>

        {bulkMode && picked.size > 0 && (
          <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--hair)', background: 'var(--surface-2)', display: 'flex', alignItems: 'center', gap: 12, fontSize: 12 }}>
            <span style={{ fontFamily: 'var(--f-mono)', color: 'var(--hi)' }}>{picked.size} selected</span>
            <span style={{ color: 'var(--dim)' }}>→ assign tier</span>
            {['S','A','B','C'].map(t => (
              <button key={t} className="btn" style={{ padding: '2px 8px' }}>
                <span className={`tier ${t}`}>{t}</span> <span className="kbd">{({S:'1',A:'2',B:'3',C:'4'})[t]}</span>
              </button>
            ))}
            <button className="btn accent" style={{ marginLeft: 'auto' }}>Commit <span className="kbd" style={{ borderColor: 'color-mix(in oklab, var(--accent) 30%, var(--hair))' }}>⏎</span></button>
          </div>
        )}

        <div ref={listRef} style={{ overflow: 'auto', flex: 1 }}>
          {filtered.map(e => (
            <div
              key={e.id}
              data-id={e.id}
              className="row"
              aria-selected={e.id === selected}
              onClick={() => setSelected(e.id)}
              style={{ gridTemplateColumns: bulkMode ? '20px 22px 1fr 70px 44px' : '22px 1fr 70px 44px' }}
            >
              {bulkMode && (
                <span onClick={(ev) => { ev.stopPropagation(); setPicked(p => { const n = new Set(p); n.has(e.id) ? n.delete(e.id) : n.add(e.id); return n; }); }}
                  style={{ width: 10, height: 10, border: '1px solid var(--hair-hi)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: picked.has(e.id) ? 'var(--accent)' : 'var(--bg)' }}>
                  {picked.has(e.id) && <svg width="8" height="8" viewBox="0 0 8 8"><path d="M1 4 L3 6 L7 2" fill="none" stroke="var(--bg)" strokeWidth="1.5" /></svg>}
                </span>
              )}
              <span className={`tier ${e.tier}`}>{e.tier}</span>
              <div style={{ minWidth: 0 }}>
                <div className="file" style={{ fontFamily: 'var(--f-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.file}</div>
                <div className="desc" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.desc}</div>
              </div>
              <div className="meta">
                <div style={{ color: e.decay > 0.5 ? 'var(--amber)' : 'var(--dim)' }}>decay {e.decay.toFixed(2)}</div>
                <div>{relTime(e.lastUsed)}</div>
              </div>
              <div className="meta" style={{ textAlign: 'right' }}>
                <div style={{ color: 'var(--fg)' }}>{e.uses}</div>
                <div style={{ textTransform: 'uppercase', letterSpacing: '0.08em' }}>{e.type}</div>
              </div>
            </div>
          ))}
        </div>
      </main>

      {/* PANE 3 — detail */}
      <aside style={{ borderLeft: '1px solid var(--hair)', overflow: 'auto' }}>
        {entry && <EntryDetail entry={entry} />}
      </aside>
    </div>
  );
}

function EntryDetail({ entry }) {
  const D = window.KONTEXT_DATA;
  const related = entry.relations.map(id => D.entries.find(e => e.id === id)).filter(Boolean);

  // Tiny local relations graph (mini)
  return (
    <div className="mount">
      {/* Header */}
      <header style={{ padding: '16px 18px', borderBottom: '1px solid var(--hair)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span className={`tier ${entry.tier}`}>{entry.tier}</span>
          <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{entry.type}</span>
          <span style={{ marginLeft: 'auto', fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>id {entry.id}</span>
        </div>
        <div style={{ fontFamily: 'var(--f-mono)', fontSize: 15, color: 'var(--hi)' }}>{entry.file}</div>
        <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 2 }}>{entry.desc}</div>
      </header>

      {/* Metrics strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderBottom: '1px solid var(--hair)' }}>
        {[
          { k: 'uses',    v: entry.uses },
          { k: 'decay',   v: entry.decay.toFixed(2), warn: entry.decay > 0.5 },
          { k: 'last',    v: relTime(entry.lastUsed) },
          { k: 'age',     v: relTime(entry.created) },
        ].map((m, i) => (
          <div key={m.k} style={{ padding: '10px 14px', borderRight: i < 3 ? '1px solid var(--hair)' : 'none' }}>
            <div className="lbl lbl-sm">{m.k}</div>
            <div style={{ fontFamily: 'var(--f-mono)', fontSize: 15, color: m.warn ? 'var(--amber)' : 'var(--hi)', marginTop: 2 }}>{m.v}</div>
          </div>
        ))}
      </div>

      {/* Body */}
      <section style={{ padding: '14px 18px', borderBottom: '1px solid var(--hair)' }}>
        <div className="lbl" style={{ marginBottom: 8 }}>Body</div>
        <pre style={{ margin: 0, fontFamily: 'var(--f-mono)', fontSize: 12, color: 'var(--fg)', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{entry.body}</pre>
      </section>

      {/* Why captured */}
      <section style={{ padding: '14px 18px', borderBottom: '1px solid var(--hair)' }}>
        <div className="lbl" style={{ marginBottom: 8 }}>Why this was captured</div>
        <div style={{ fontSize: 12, color: 'var(--fg)' }}>{entry.why}</div>
      </section>

      {/* Relations mini-graph */}
      <section style={{ padding: '14px 18px', borderBottom: '1px solid var(--hair)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <span className="lbl">Relations</span>
          <button className="btn ghost" style={{ padding: '2px 6px', fontSize: 11, color: 'var(--dim)' }} onClick={() => window.__nav && window.__nav('relations', entry.id)}>
            open graph →
          </button>
        </div>
        {related.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--dim)', fontFamily: 'var(--f-mono)' }}>— none —</div>
        ) : (
          <div>
            {related.map(r => (
              <div key={r.id} style={{ display: 'grid', gridTemplateColumns: '18px 1fr auto', gap: 8, padding: '4px 0', fontSize: 12, alignItems: 'center' }}>
                <span className={`tier ${r.tier}`} style={{ width: 16, height: 16, fontSize: 10 }}>{r.tier}</span>
                <span style={{ fontFamily: 'var(--f-mono)', color: 'var(--hi)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.file}</span>
                <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>{r.uses}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Edit history */}
      <section style={{ padding: '14px 18px' }}>
        <div className="lbl" style={{ marginBottom: 8 }}>Edit history</div>
        <div style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--fg)' }}>
          {[
            { t: relTime(entry.lastUsed), who: 'claude-code', act: 'read' },
            { t: '2d', who: 'hook:relations', act: `linked ↔ ${related[0]?.file || '—'}` },
            { t: '6d', who: 'claude-code', act: 'appended 142b' },
            { t: '14d', who: 'manual', act: `tier ${entry.tier === 'S' ? 'A' : 'B'} → ${entry.tier}` },
            { t: relTime(entry.created), who: 'capture', act: 'created' },
          ].map((h, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '40px 100px 1fr', gap: 10, padding: '3px 0', color: 'var(--dim)' }}>
              <span>{h.t}</span>
              <span style={{ color: 'var(--fg)' }}>{h.who}</span>
              <span>{h.act}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

window.Entries = Entries;
