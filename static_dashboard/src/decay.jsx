// Decay — "Cleanup" — plain-language review of memories Kontext is forgetting.
//
// Design intent: a layperson should be able to land here and immediately
// understand "these are old notes, do you still want them?" — no decay scores,
// no tier letters, no lambda. The full data is one keystroke away (D for details).

function Decay() {
  const D = window.KONTEXT_DATA;
  const [showDetails, setShowDetails] = useState(false);

  // Build the queue + group by friendly buckets
  const queue = useMemo(
    () => [...D.entries].sort((a, b) => b.decay - a.decay).filter(e => e.decay > 0.15),
    []
  );

  const bucketFor = (d) =>
    d >= 0.7 ? 'fading'   :
    d >= 0.5 ? 'cooling'  :
    d >= 0.3 ? 'quiet'    : 'watch';

  const BUCKETS = {
    fading:  { label: 'Almost forgotten', sub: 'Not used in over a month',     color: 'var(--amber)' },
    cooling: { label: 'Cooling off',      sub: 'Hasn\u2019t come up in weeks', color: 'var(--fg)' },
    quiet:   { label: 'Quiet',            sub: 'Slipping out of regular use',  color: 'var(--dim)' },
    watch:   { label: 'On watch',         sub: 'Just keeping an eye on these', color: 'var(--dim)' },
  };

  const grouped = useMemo(() => {
    const g = { fading: [], cooling: [], quiet: [], watch: [] };
    queue.forEach(e => g[bucketFor(e.decay)].push(e));
    return g;
  }, [queue]);

  const [cursor, setCursor] = useState(0);
  const [decisions, setDecisions] = useState({});
  const current = queue[cursor];

  const decide = (verdict) => {
    setDecisions(prev => ({ ...prev, [current.id]: verdict }));
    if (cursor < queue.length - 1) setCursor(cursor + 1);
  };
  const skip = () => { if (cursor < queue.length - 1) setCursor(cursor + 1); };

  useKey(['j','arrowdown'], (e) => { e.preventDefault(); setCursor(c => Math.min(queue.length - 1, c + 1)); }, [queue.length]);
  useKey(['k','arrowup'],   (e) => { e.preventDefault(); setCursor(c => Math.max(0, c - 1)); }, []);
  useKey(['y'], () => current && decide('keep'),    [current]);
  useKey(['n'], () => current && decide('letgo'),   [current]);
  useKey(['s'], () => skip(),                        [cursor, queue.length]);
  useKey(['d'], () => setShowDetails(s => !s),       []);

  const counts = {
    keep:  Object.values(decisions).filter(v => v === 'keep').length,
    letgo: Object.values(decisions).filter(v => v === 'letgo').length,
  };
  const reviewed = counts.keep + counts.letgo;

  // Friendly relative-age sentence
  const ageSentence = (entry) => {
    const last = relTime(entry.lastUsed);
    if (entry.decay >= 0.7)  return `Hasn\u2019t come up in ${last}.`;
    if (entry.decay >= 0.5)  return `Last used ${last} ago.`;
    return `Quiet for about ${last}.`;
  };

  // ---------- Empty state ----------
  if (queue.length === 0) {
    return (
      <div className="mount" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <div style={{ textAlign: 'center', maxWidth: 360 }}>
          <div style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: '0.2em', marginBottom: 16 }}>All clear</div>
          <div style={{ fontSize: 22, color: 'var(--hi)', marginBottom: 10, letterSpacing: '-0.01em' }}>Nothing to review.</div>
          <div style={{ fontSize: 13, color: 'var(--dim)', lineHeight: 1.6 }}>
            Every memory in your library has been used recently. Come back when something has been quiet for a while.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mount" style={{ display: 'grid', gridTemplateColumns: '320px 1fr', height: '100%' }}>

      {/* ============ LEFT — grouped list ============ */}
      <aside style={{ borderRight: '1px solid var(--hair)', display: 'flex', flexDirection: 'column' }}>
        <header style={{ padding: '20px 20px 16px' }}>
          <div className="lbl" style={{ marginBottom: 6 }}>Cleanup</div>
          <div style={{ fontSize: 14, color: 'var(--hi)', lineHeight: 1.5 }}>
            <span style={{ fontFamily: 'var(--f-mono)', color: 'var(--hi)' }}>{queue.length}</span>
            {' '}older notes that haven\u2019t come up lately.
          </div>
          <div style={{ fontSize: 12, color: 'var(--dim)', marginTop: 6, lineHeight: 1.5 }}>
            Decide what\u2019s still worth remembering.
          </div>
        </header>

        <div style={{ overflow: 'auto', flex: 1, borderTop: '1px solid var(--hair)' }}>
          {Object.entries(grouped).map(([key, items]) => {
            if (items.length === 0) return null;
            const b = BUCKETS[key];
            return (
              <div key={key}>
                <div style={{
                  padding: '14px 20px 8px',
                  borderTop: '1px solid var(--hair)',
                  display: 'grid',
                  gridTemplateColumns: '6px 1fr auto',
                  gap: 10,
                  alignItems: 'baseline',
                }}>
                  <span style={{ width: 4, height: 4, borderRadius: '50%', background: b.color, alignSelf: 'center' }} />
                  <div>
                    <div style={{ fontSize: 12, color: 'var(--hi)' }}>{b.label}</div>
                    <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{b.sub}</div>
                  </div>
                  <span style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--dim)' }}>{items.length}</span>
                </div>
                {items.map(e => {
                  const i = queue.indexOf(e);
                  const decision = decisions[e.id];
                  const isCur = i === cursor;
                  return (
                    <div key={e.id} onClick={() => setCursor(i)}
                      style={{
                        padding: '10px 20px 10px 30px',
                        cursor: 'pointer',
                        background: isCur ? 'var(--surface-2)' : 'transparent',
                        boxShadow: isCur ? 'inset 2px 0 0 var(--accent)' : 'none',
                        opacity: decision ? 0.45 : 1,
                      }}>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                        <span style={{ fontFamily: 'var(--f-mono)', fontSize: 12, color: 'var(--hi)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, textDecoration: decision === 'letgo' ? 'line-through' : 'none' }}>
                          {prettyName(e.file)}
                        </span>
                        {decision && (
                          <span style={{ fontFamily: 'var(--f-mono)', fontSize: 9, color: decision === 'keep' ? 'var(--accent)' : 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                            {decision === 'keep' ? '\u2713 keep' : '\u2715 let go'}
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {e.desc}
                      </div>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>

        {/* Progress footer */}
        <div style={{ padding: '14px 20px', borderTop: '1px solid var(--hair)', background: 'var(--surface)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
            <span className="lbl">Progress</span>
            <span style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--hi)' }}>
              {reviewed} <span style={{ color: 'var(--dim)' }}>/ {queue.length}</span>
            </span>
          </div>
          <div style={{ position: 'relative', height: 2, background: 'var(--hair)', marginBottom: 10 }}>
            <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${(reviewed / queue.length) * 100}%`, background: 'var(--accent)' }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>
            <span><span style={{ color: 'var(--accent)' }}>{counts.keep}</span> kept</span>
            <span>{counts.letgo} let go</span>
            <span>{queue.length - reviewed} left</span>
          </div>
        </div>
      </aside>

      {/* ============ RIGHT — focused card ============ */}
      <main style={{ display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>

        {/* Top strip — tiny context */}
        <div style={{ padding: '14px 32px', borderBottom: '1px solid var(--hair)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--dim)' }}>
            Reviewing <span style={{ color: 'var(--hi)' }}>{cursor + 1}</span> of <span style={{ color: 'var(--hi)' }}>{queue.length}</span>
          </div>
          <button
            className="btn ghost"
            onClick={() => setShowDetails(s => !s)}
            style={{ fontSize: 11, color: 'var(--dim)' }}
          >
            {showDetails ? 'Hide' : 'Show'} technical details <span className="kbd">D</span>
          </button>
        </div>

        {current && (
          <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>

            {/* HERO — the card */}
            <section style={{ padding: '56px 64px 40px', maxWidth: 760, width: '100%' }}>

              {/* Status pill */}
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginBottom: 28 }}>
                <span style={{
                  width: 6, height: 6, borderRadius: '50%',
                  background: BUCKETS[bucketFor(current.decay)].color,
                }} />
                <span style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.16em' }}>
                  {BUCKETS[bucketFor(current.decay)].label}
                </span>
              </div>

              {/* Big plain-language headline */}
              <h1 style={{ margin: 0, fontSize: 32, fontWeight: 400, color: 'var(--hi)', letterSpacing: '-0.02em', lineHeight: 1.2 }}>
                {prettyName(current.file)}
              </h1>
              <div style={{ marginTop: 12, fontSize: 16, color: 'var(--fg)', lineHeight: 1.5 }}>
                {current.desc}
              </div>

              {/* The friendly summary — three short facts */}
              <div style={{ marginTop: 36, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 24, paddingTop: 24, borderTop: '1px solid var(--hair)' }}>
                <Fact label="Last came up"  value={`${relTime(current.lastUsed)} ago`} />
                <Fact label="Used in total" value={`${current.uses} time${current.uses === 1 ? '' : 's'}`} />
                <Fact label="Connected to"  value={`${current.relations.length} other note${current.relations.length === 1 ? '' : 's'}`} />
              </div>

              {/* Why we're asking */}
              <div style={{ marginTop: 32, padding: '16px 18px', border: '1px solid var(--hair)', borderLeft: '2px solid var(--accent)' }}>
                <div className="lbl" style={{ marginBottom: 6 }}>Why you\u2019re seeing this</div>
                <div style={{ fontSize: 13, color: 'var(--fg)', lineHeight: 1.55 }}>
                  {ageSentence(current)}{' '}
                  {current.relations.length === 0
                    ? 'It isn\u2019t connected to anything else, so it doesn\u2019t come up by association either.'
                    : current.uses < 10
                      ? 'It hasn\u2019t been used much over its lifetime.'
                      : 'It used to come up often, but the topic has gone quiet.'}
                </div>
              </div>

              {/* What's inside (collapsible body) */}
              <details style={{ marginTop: 24 }} open>
                <summary style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.16em', cursor: 'pointer', padding: '6px 0', listStyle: 'none' }}>
                  What\u2019s in this note
                </summary>
                <pre style={{ margin: '10px 0 0', fontFamily: 'var(--f-mono)', fontSize: 12, color: 'var(--fg)', whiteSpace: 'pre-wrap', lineHeight: 1.65, paddingLeft: 14, borderLeft: '1px solid var(--hair)' }}>
                  {current.body}
                </pre>
              </details>

              {/* Optional technical drawer */}
              {showDetails && (
                <div style={{ marginTop: 28, padding: '16px 18px', background: 'var(--surface)', border: '1px solid var(--hair)' }}>
                  <div className="lbl" style={{ marginBottom: 12 }}>Technical details</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 14, fontFamily: 'var(--f-mono)', fontSize: 11 }}>
                    <Tech k="file"     v={current.file} />
                    <Tech k="id"       v={current.id} />
                    <Tech k="type"     v={current.type} />
                    <Tech k="tier"     v={current.tier} />
                    <Tech k="decay"    v={`${current.decay.toFixed(2)} / 1.00`} warn={current.decay > 0.5} />
                    <Tech k="\u03bb"   v={({S:0.004,A:0.008,B:0.02,C:0.05})[current.tier]} />
                    <Tech k="created"  v={`${relTime(current.created)} ago`} />
                    <Tech k="last_used" v={`${relTime(current.lastUsed)} ago`} />
                  </div>
                  <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--hair)', fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--dim)', lineHeight: 1.6 }}>
                    score = 1 \u2212 e^(\u2212\u03bb \u00b7 days_since_use)\u00a0\u00b7\u00a0capped at 1.00
                  </div>
                </div>
              )}
            </section>
          </div>
        )}

        {/* ============ ACTION BAR ============ */}
        {current && (
          <div style={{
            padding: '18px 32px',
            borderTop: '1px solid var(--hair)',
            background: 'var(--surface)',
            display: 'grid',
            gridTemplateColumns: '1fr auto 1fr',
            gap: 16,
            alignItems: 'center',
          }}>
            {/* Let go */}
            <div>
              <button
                className="btn"
                onClick={() => decide('letgo')}
                style={{ width: '100%', padding: '12px 16px', justifyContent: 'space-between', borderColor: 'var(--hair-hi)' }}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 14, color: 'var(--hi)' }}>Let go</span>
                  <span style={{ fontSize: 11, color: 'var(--dim)' }}>archive this memory</span>
                </span>
                <span className="kbd">N</span>
              </button>
            </div>

            {/* Skip */}
            <button
              className="btn ghost"
              onClick={skip}
              style={{ padding: '10px 14px', fontSize: 12, color: 'var(--dim)' }}
            >
              Decide later <span className="kbd" style={{ marginLeft: 6 }}>S</span>
            </button>

            {/* Keep */}
            <div>
              <button
                className="btn accent"
                onClick={() => decide('keep')}
                style={{ width: '100%', padding: '12px 16px', justifyContent: 'space-between' }}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 14 }}>Keep</span>
                  <span style={{ fontSize: 11, opacity: 0.7 }}>still useful — refresh it</span>
                </span>
                <span className="kbd" style={{ borderColor: 'color-mix(in oklab, var(--accent) 30%, var(--hair))' }}>Y</span>
              </button>
            </div>
          </div>
        )}

        {/* Tiny keyboard hint row */}
        <div style={{ padding: '8px 32px 12px', display: 'flex', justifyContent: 'center', gap: 18, fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>
          <span><span className="kbd">Y</span> keep</span>
          <span><span className="kbd">N</span> let go</span>
          <span><span className="kbd">S</span> skip</span>
          <span><span className="kbd">J</span>/<span className="kbd">K</span> next/prev</span>
          <span><span className="kbd">D</span> details</span>
        </div>
      </main>
    </div>
  );
}

// ————— small pieces —————
function Fact({ label, value }) {
  return (
    <div>
      <div className="lbl" style={{ marginBottom: 6 }}>{label}</div>
      <div style={{ fontFamily: 'var(--f-mono)', fontSize: 16, color: 'var(--hi)', letterSpacing: '-0.01em' }}>{value}</div>
    </div>
  );
}

function Tech({ k, v, warn }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
      <span style={{ color: 'var(--dim)' }}>{k}</span>
      <span style={{ color: warn ? 'var(--amber)' : 'var(--hi)', textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis' }}>{v}</span>
    </div>
  );
}

// "user_identity.md" → "User identity"
function prettyName(file) {
  return file
    .replace(/\.md$/i, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (c, i) => i === 0 ? c.toUpperCase() : c.toLowerCase());
}

window.Decay = Decay;
