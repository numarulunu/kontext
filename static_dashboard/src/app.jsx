// App shell — topbar, routing, cmd+k, tweaks

const TWEAKS_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "phosphor",
  "density": "normal",
  "gridGuides": false,
  "scoreStyle": "radar"
}/*EDITMODE-END*/;

const ACCENTS = {
  none:     { v: 'oklch(0.85 0 0)',      label: 'none (white)' },
  phosphor: { v: 'oklch(0.85 0.15 150)', label: 'phosphor' },
  amber:    { v: 'oklch(0.82 0.14 75)',  label: 'amber' },
  blue:     { v: 'oklch(0.75 0.15 240)', label: 'blue' },
};

function App() {
  const [route, setRoute] = useState(() => {
    try {
      let r = localStorage.getItem('kontext.route') || 'overview';
      // Legacy routes that were removed
      if (r === 'doc' || r === 'top') r = 'overview';
      return ['overview','entries','relations','decay','settings'].includes(r) ? r : 'overview';
    } catch { return 'overview'; }
  });
  const [routeCtx, setRouteCtx] = useState(null);
  const [cmdkOpen, setCmdkOpen] = useState(false);
  const [tweaksOpen, setTweaksOpen] = useState(false);
  const [tweaks, setTweaks] = useState(TWEAKS_DEFAULTS);
  const [gotoLeader, setGotoLeader] = useState(false);

  // Expose nav globally (used by deep-links from other pages)
  useEffect(() => {
    window.__nav = (r, ctx) => { setRoute(r); setRouteCtx(ctx || null); };
  }, []);

  // Persist route
  useEffect(() => {
    try { localStorage.setItem('kontext.route', route); } catch {}
  }, [route]);

  // Apply tweaks to body + css vars
  useEffect(() => {
    const body = document.body;
    body.dataset.density = tweaks.density;
    body.classList.toggle('grid-guides', !!tweaks.gridGuides);
    document.documentElement.style.setProperty('--accent', ACCENTS[tweaks.accent]?.v || ACCENTS.phosphor.v);
  }, [tweaks]);

  // Edit-mode protocol
  useEffect(() => {
    const onMsg = (e) => {
      if (e.data?.type === '__activate_edit_mode') setTweaksOpen(true);
      if (e.data?.type === '__deactivate_edit_mode') setTweaksOpen(false);
    };
    window.addEventListener('message', onMsg);
    window.parent.postMessage({ type: '__edit_mode_available' }, '*');
    return () => window.removeEventListener('message', onMsg);
  }, []);

  const updateTweak = (k, v) => {
    setTweaks(prev => {
      const next = { ...prev, [k]: v };
      window.parent.postMessage({ type: '__edit_mode_set_keys', edits: { [k]: v } }, '*');
      return next;
    });
  };

  // Global hotkeys
  useKey(['mod+k'], (e) => { e.preventDefault(); setCmdkOpen(o => !o); }, []);
  useKey(['escape'], () => { setCmdkOpen(false); setGotoLeader(false); }, []);
  useKey(['g'], () => setGotoLeader(true), []);
  useKey(['o','e','r','c','s'], (e) => {
    if (!gotoLeader) return;
    setGotoLeader(false);
    const map = { o: 'overview', e: 'entries', r: 'relations', c: 'decay', s: 'settings' };
    if (map[e.key]) { setRoute(map[e.key]); setRouteCtx(null); e.preventDefault(); }
  }, [gotoLeader]);

  // Release leader after 1.2s
  useEffect(() => {
    if (!gotoLeader) return;
    const id = setTimeout(() => setGotoLeader(false), 1200);
    return () => clearTimeout(id);
  }, [gotoLeader]);

  const Page =
    route === 'overview'  ? <Overview /> :
    route === 'entries'   ? <Entries initialId={routeCtx} /> :
    route === 'relations' ? <Relations focusId={routeCtx} /> :
    route === 'decay'     ? <Decay /> :
    route === 'settings'  ? <Settings /> :
                            <Overview />;

  return (
    <div className="app">
      {/* TOPBAR */}
      <div className="topbar">
        <div className="brand">
          <div className="mark" />
          <div className="name">kontext<span className="dim"> / ionut</span></div>
        </div>
        <nav className="tabs">
          {[
            { k: 'overview',  l: 'Overview',   h: 'GO' },
            { k: 'entries',   l: 'Entries',    h: 'GE' },
            { k: 'relations', l: 'Relations',  h: 'GR' },
            { k: 'decay',     l: 'Cleanup',    h: 'GC' },
            { k: 'settings',  l: 'Settings',   h: 'GS' },
          ].map(t => (
            <button key={t.k} className="tab" aria-current={route === t.k}
              onClick={() => { setRoute(t.k); setRouteCtx(null); }}>
              {t.l}<span className="kbd">{t.h}</span>
            </button>
          ))}
        </nav>
        <div className="right">
          <div className="chip">
            <span className="dot pulse" />
            <span>live · 3 devices</span>
          </div>
          <button className="cmd" onClick={() => setCmdkOpen(true)}>
            <span>Search or command…</span>
            <span className="kbd">⌘K</span>
          </button>
        </div>
      </div>

      {/* MAIN */}
      <div className="main">
        {Page}
        {gotoLeader && (
          <div style={{ position: 'fixed', bottom: 40, left: '50%', transform: 'translateX(-50%)', zIndex: 40, background: 'var(--surface)', border: '1px solid var(--hair-hi)', padding: '8px 14px', fontFamily: 'var(--f-mono)', fontSize: 12, color: 'var(--dim)' }}>
            g · <span style={{ color: 'var(--hi)' }}>o</span>verview · <span style={{ color: 'var(--hi)' }}>e</span>ntries · <span style={{ color: 'var(--hi)' }}>r</span>elations · <span style={{ color: 'var(--hi)' }}>c</span>leanup · <span style={{ color: 'var(--hi)' }}>s</span>ettings
          </div>
        )}
      </div>

      {/* STATUSBAR */}
      <div className="statusbar">
        <div className="seg"><span className="key">kontext</span><span className="val">v0.8.3-alpha</span></div>
        <div className="seg"><span className="key">sync</span><span className="val">litefs · ok · 12s</span></div>
        <div className="seg"><span className="key">last op</span><span className="val">capture · {relTime(NOW - 4*60*1000)} ago</span></div>
        <div className="seg"><span className="key">entries</span><span className="val">{window.KONTEXT_DATA.totals.entries}</span></div>
        <div className="seg"><span className="key">score</span><span className="val">{window.KONTEXT_DATA.score}</span></div>
        <div className="seg"><span className="kbd">⌘K</span><span className="kbd">G O</span><span className="kbd">/</span><span className="kbd">J K</span></div>
      </div>

      {cmdkOpen && <CmdK onClose={() => setCmdkOpen(false)} onNav={(r, ctx) => { setRoute(r); setRouteCtx(ctx); setCmdkOpen(false); }} />}

      {tweaksOpen && <TweaksPanel tweaks={tweaks} update={updateTweak} onClose={() => setTweaksOpen(false)} />}
    </div>
  );
}

// ————— Cmd+K palette —————
function CmdK({ onClose, onNav }) {
  const D = window.KONTEXT_DATA;
  const [q, setQ] = useState('');
  const [cursor, setCursor] = useState(0);

  const commands = useMemo(() => [
    { kind: 'nav', label: 'Go to Overview',  hint: 'G O', act: () => onNav('overview') },
    { kind: 'nav', label: 'Go to Entries',   hint: 'G E', act: () => onNav('entries') },
    { kind: 'nav', label: 'Go to Relations', hint: 'G R', act: () => onNav('relations') },
    { kind: 'nav', label: 'Go to Cleanup', hint: 'G C', act: () => onNav('decay') },
    { kind: 'nav', label: 'Go to Settings', hint: 'G S', act: () => onNav('settings') },
    { kind: 'act', label: 'Toggle bulk tier review', hint: 'B',  act: () => onNav('entries') },
    { kind: 'act', label: 'Export library (JSON)', hint: '',    act: onClose },
    { kind: 'act', label: 'Run decay cron now',    hint: '',    act: onClose },
    { kind: 'act', label: 'Rebuild FTS5 index',    hint: '',    act: onClose },
    { kind: 'act', label: 'Invite user…',           hint: '',    act: onClose },
  ], []);

  const entryItems = useMemo(() => D.entries.map(e => ({
    kind: 'entry', label: e.file, sub: e.desc, tier: e.tier, act: () => onNav('entries', e.id)
  })), []);

  const items = useMemo(() => {
    const qq = q.trim().toLowerCase();
    const all = [...commands, ...entryItems];
    if (!qq) return commands.concat(entryItems.slice(0, 8));
    return all.filter(it => (it.label + ' ' + (it.sub || '')).toLowerCase().includes(qq)).slice(0, 14);
  }, [q]);

  useEffect(() => { setCursor(0); }, [q]);

  useKey(['arrowdown'], (e) => { e.preventDefault(); setCursor(c => Math.min(items.length - 1, c + 1)); }, [items.length]);
  useKey(['arrowup'],   (e) => { e.preventDefault(); setCursor(c => Math.max(0, c - 1)); }, []);
  useKey(['enter'],     (e) => { const it = items[cursor]; if (it) { e.preventDefault(); it.act(); } }, [items, cursor]);

  return (
    <div className="cmdk-backdrop" onClick={onClose}>
      <div className="cmdk" onClick={e => e.stopPropagation()}>
        <div className="input-row">
          <Icon d={Icons.search} />
          <input autoFocus value={q} onChange={e => setQ(e.target.value)} placeholder="Search entries or run a command…" />
          <span className="kbd">ESC</span>
        </div>
        <div className="list">
          {items.length === 0 && (
            <div style={{ padding: '14px', color: 'var(--dim)', fontSize: 12 }}>No matches.</div>
          )}
          {items.map((it, i) => (
            <div key={i} className="item" aria-selected={i === cursor}
              onMouseEnter={() => setCursor(i)} onClick={() => it.act()}>
              <span>
                {it.kind === 'nav' && <Icon d={Icons.arrow} />}
                {it.kind === 'act' && <Icon d={Icons.plus} />}
                {it.kind === 'entry' && <span className={`tier ${it.tier}`} style={{ width: 14, height: 14, fontSize: 9 }}>{it.tier}</span>}
              </span>
              <span>
                <span style={{ color: 'var(--hi)', fontFamily: it.kind === 'entry' ? 'var(--f-mono)' : 'inherit' }}>{it.label}</span>
                {it.sub && <span style={{ color: 'var(--dim)', marginLeft: 8 }}>· {it.sub}</span>}
              </span>
              <span className="hint">{it.hint || <span className="kind">{it.kind}</span>}</span>
            </div>
          ))}
        </div>
        <div style={{ padding: '8px 14px', borderTop: '1px solid var(--hair)', display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>
          <span>{items.length} results</span>
          <span><span className="kbd">↑↓</span> nav · <span className="kbd">⏎</span> run · <span className="kbd">ESC</span> close</span>
        </div>
      </div>
    </div>
  );
}

// ————— Tweaks panel —————
function TweaksPanel({ tweaks, update, onClose }) {
  return (
    <div className="tweaks">
      <header>
        <span className="lbl">Tweaks</span>
        <button onClick={onClose} style={{ color: 'var(--dim)' }}><Icon d={Icons.close} size={10} /></button>
      </header>
      <div className="row-t">
        <label>Accent</label>
        <div className="swatches">
          {Object.entries(ACCENTS).map(([k, a]) => (
            <button key={k} className={`sw ${tweaks.accent === k ? 'active' : ''}`} title={a.label} style={{ background: a.v }} onClick={() => update('accent', k)} />
          ))}
        </div>
      </div>
      <div className="row-t">
        <label>Density</label>
        <div className="seg-ctrl">
          {['compact','normal','comfort'].map(d => (
            <button key={d} className={tweaks.density === d ? 'on' : ''} onClick={() => update('density', d)}>{d}</button>
          ))}
        </div>
      </div>
      <div className="row-t">
        <label>Grid guides</label>
        <div className="seg-ctrl">
          <button className={!tweaks.gridGuides ? 'on' : ''} onClick={() => update('gridGuides', false)}>off</button>
          <button className={tweaks.gridGuides ? 'on' : ''} onClick={() => update('gridGuides', true)}>on</button>
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
