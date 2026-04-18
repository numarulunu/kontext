// Relations graph — force-directed (hand-rolled, no libs)

function Relations({ focusId }) {
  const D = window.KONTEXT_DATA;
  const W = 900, H = 640;
  const [nodes, setNodes] = useState(null);
  const [hover, setHover] = useState(null);
  const [hoverPos, setHoverPos] = useState({ x: 0, y: 0 });
  const [selected, setSelected] = useState(focusId || null);

  // Build once — seed deterministic
  useEffect(() => {
    const N = D.entries.map((e, i) => {
      const a = (i / D.entries.length) * Math.PI * 2;
      const r = 140 + (e.tier === 'S' ? 0 : e.tier === 'A' ? 60 : e.tier === 'B' ? 140 : 220);
      return { ...e, x: W/2 + Math.cos(a) * r, y: H/2 + Math.sin(a) * r, vx: 0, vy: 0 };
    });
    const edges = [];
    N.forEach(n => n.relations.forEach(rid => {
      if (n.id < rid) edges.push([n.id, rid]);
    }));

    // Run 200 iterations of a tiny force sim
    for (let iter = 0; iter < 280; iter++) {
      // Repulsion
      for (let i = 0; i < N.length; i++) {
        for (let j = i+1; j < N.length; j++) {
          const dx = N[i].x - N[j].x, dy = N[i].y - N[j].y;
          const d2 = dx*dx + dy*dy + 0.01;
          const d = Math.sqrt(d2);
          const f = 2400 / d2;
          N[i].vx += (dx/d) * f; N[i].vy += (dy/d) * f;
          N[j].vx -= (dx/d) * f; N[j].vy -= (dy/d) * f;
        }
      }
      // Springs
      edges.forEach(([a, b]) => {
        const A = N.find(n => n.id === a), B = N.find(n => n.id === b);
        const dx = B.x - A.x, dy = B.y - A.y;
        const d = Math.sqrt(dx*dx + dy*dy) + 0.01;
        const target = 110;
        const f = (d - target) * 0.04;
        A.vx += (dx/d) * f; A.vy += (dy/d) * f;
        B.vx -= (dx/d) * f; B.vy -= (dy/d) * f;
      });
      // Gravity to center
      N.forEach(n => {
        n.vx += (W/2 - n.x) * 0.003;
        n.vy += (H/2 - n.y) * 0.003;
        n.x += n.vx * 0.4; n.y += n.vy * 0.4;
        n.vx *= 0.7; n.vy *= 0.7;
      });
    }
    setNodes({ N, edges });
  }, []);

  if (!nodes) return <div style={{ padding: 40, color: 'var(--dim)', fontFamily: 'var(--f-mono)', fontSize: 12 }}>computing layout…</div>;

  const tierRadius = { S: 7, A: 5.5, B: 4.5, C: 3.5 };
  const activeNode = hover || selected;
  const neighbours = new Set();
  if (activeNode) {
    const n = nodes.N.find(x => x.id === activeNode);
    if (n) n.relations.forEach(r => neighbours.add(r));
    neighbours.add(activeNode);
  }

  return (
    <div className="mount" style={{ display: 'grid', gridTemplateColumns: '1fr 320px', height: '100%' }}>
      <div style={{ position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: 14, left: 16, zIndex: 2 }}>
          <div className="lbl">Relations graph</div>
          <div style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)', marginTop: 2 }}>
            {nodes.N.length} nodes · {nodes.edges.length} edges · force-directed
          </div>
        </div>
        <div style={{ position: 'absolute', top: 14, right: 16, zIndex: 2, display: 'flex', gap: 16, fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>
          <span><span style={{ display: 'inline-block', width: 7, height: 7, background: 'var(--hi)', borderRadius: '50%', verticalAlign: 'middle', marginRight: 5 }} />S</span>
          <span><span style={{ display: 'inline-block', width: 6, height: 6, background: 'var(--fg)', borderRadius: '50%', verticalAlign: 'middle', marginRight: 5 }} />A</span>
          <span><span style={{ display: 'inline-block', width: 5, height: 5, background: 'var(--dim)', borderRadius: '50%', verticalAlign: 'middle', marginRight: 5 }} />B</span>
          <span><span style={{ display: 'inline-block', width: 4, height: 4, background: 'var(--hair-hi)', borderRadius: '50%', verticalAlign: 'middle', marginRight: 5 }} />C</span>
          <span style={{ borderLeft: '1px solid var(--hair)', paddingLeft: 12 }}>orphan 6</span>
        </div>

        <svg width="100%" height="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }}>
          {/* subtle grid */}
          <defs>
            <pattern id="g" width="40" height="40" patternUnits="userSpaceOnUse">
              <path d="M 40 0 L 0 0 0 40" fill="none" stroke="var(--hair)" strokeWidth="0.5" />
            </pattern>
          </defs>
          <rect width={W} height={H} fill="url(#g)" opacity="0.4" />

          {/* edges */}
          {nodes.edges.map(([a, b], i) => {
            const A = nodes.N.find(n => n.id === a);
            const B = nodes.N.find(n => n.id === b);
            const highlighted = activeNode && (a === activeNode || b === activeNode);
            return (
              <line key={i} x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                stroke={highlighted ? 'var(--accent)' : 'var(--hair-hi)'}
                strokeWidth={highlighted ? 1 : 0.5}
                opacity={activeNode && !highlighted ? 0.2 : 0.9} />
            );
          })}

          {/* nodes */}
          {nodes.N.map(n => {
            const r = tierRadius[n.tier];
            const dim = activeNode && !neighbours.has(n.id);
            return (
              <g key={n.id}
                onMouseEnter={(e) => { setHover(n.id); setHoverPos({ x: e.clientX, y: e.clientY }); }}
                onMouseMove={(e) => setHoverPos({ x: e.clientX, y: e.clientY })}
                onMouseLeave={() => setHover(null)}
                onClick={() => setSelected(n.id)}
                style={{ cursor: 'pointer' }}>
                <circle cx={n.x} cy={n.y} r={r + 4} fill="var(--bg)" />
                <circle cx={n.x} cy={n.y} r={r}
                  fill={n.tier === 'S' ? 'var(--hi)' : n.tier === 'A' ? 'var(--fg)' : n.tier === 'B' ? 'var(--dim)' : 'var(--hair-hi)'}
                  opacity={dim ? 0.25 : 1}
                  stroke={n.id === activeNode ? 'var(--accent)' : 'none'} strokeWidth="1.5"
                />
                {(n.tier === 'S' || n.id === activeNode) && (
                  <text x={n.x + r + 6} y={n.y + 3} fontFamily="var(--f-mono)" fontSize="10"
                    fill={dim ? 'var(--dim)' : 'var(--hi)'}>{n.file.replace('.md','')}</text>
                )}
              </g>
            );
          })}
        </svg>

        {/* Hover preview — markdown body */}
        {hover && (() => {
          const n = nodes.N.find(x => x.id === hover);
          if (!n) return null;
          const vw = window.innerWidth, vh = window.innerHeight;
          const w = 360;
          const left = hoverPos.x + 20 + w > vw ? hoverPos.x - w - 20 : hoverPos.x + 20;
          const top  = hoverPos.y + 20 + 280 > vh ? hoverPos.y - 280 - 20 : hoverPos.y + 20;
          return (
            <div style={{
              position: 'fixed', left, top, width: w, zIndex: 40,
              background: 'var(--surface)', border: '1px solid var(--hair-hi)',
              boxShadow: '0 16px 40px rgba(0,0,0,0.5)',
              pointerEvents: 'none',
            }}>
              <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--hair)', display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className={`tier ${n.tier}`} style={{ width: 14, height: 14, fontSize: 9 }}>{n.tier}</span>
                <span style={{ fontFamily: 'var(--f-mono)', fontSize: 12, color: 'var(--hi)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{n.file}</span>
                <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{n.type}</span>
              </div>
              <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--hair)', fontSize: 11, color: 'var(--dim)' }}>{n.desc}</div>
              <pre style={{
                margin: 0, padding: '12px 14px',
                fontFamily: 'var(--f-mono)', fontSize: 11, lineHeight: 1.55,
                color: 'var(--fg)', whiteSpace: 'pre-wrap',
                maxHeight: 200, overflow: 'hidden',
              }}>{n.body}</pre>
              <div style={{ padding: '8px 14px', borderTop: '1px solid var(--hair)', fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)', display: 'flex', justifyContent: 'space-between' }}>
                <span>uses {n.uses} · decay {n.decay.toFixed(2)}</span>
                <span>click to pin</span>
              </div>
            </div>
          );
        })()}

        <div style={{ position: 'absolute', bottom: 14, left: 16, right: 16, fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)', display: 'flex', justifyContent: 'space-between' }}>
          <span>click node to pin · hover to probe</span>
          <span><span className="kbd">G</span> graph  <span className="kbd">O</span> orphans only  <span className="kbd">R</span> reseed</span>
        </div>
      </div>

      {/* Inspector */}
      <aside style={{ borderLeft: '1px solid var(--hair)', overflow: 'auto' }}>
        {activeNode ? (() => {
          const n = nodes.N.find(x => x.id === activeNode);
          const rel = n.relations.map(id => D.entries.find(e => e.id === id)).filter(Boolean);
          return (
            <div>
              <header style={{ padding: '14px 16px', borderBottom: '1px solid var(--hair)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <span className={`tier ${n.tier}`}>{n.tier}</span>
                  <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{n.type}</span>
                </div>
                <div style={{ fontFamily: 'var(--f-mono)', fontSize: 13, color: 'var(--hi)' }}>{n.file}</div>
                <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 3 }}>{n.desc}</div>
              </header>
              <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--hair)', fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--dim)' }}>
                degree <span style={{ color: 'var(--hi)' }}>{n.relations.length}</span> ·
                uses <span style={{ color: 'var(--hi)' }}>{n.uses}</span> ·
                decay <span style={{ color: n.decay > 0.5 ? 'var(--amber)' : 'var(--hi)' }}>{n.decay.toFixed(2)}</span>
              </div>
              <div style={{ padding: '12px 16px' }}>
                <div className="lbl" style={{ marginBottom: 8 }}>Neighbours</div>
                {rel.length === 0
                  ? <div style={{ fontSize: 11, color: 'var(--dim)', fontFamily: 'var(--f-mono)' }}>— orphan —</div>
                  : rel.map(r => (
                    <div key={r.id} style={{ display: 'grid', gridTemplateColumns: '18px 1fr auto', gap: 8, padding: '3px 0', cursor: 'pointer' }} onClick={() => setSelected(r.id)}>
                      <span className={`tier ${r.tier}`} style={{ width: 14, height: 14, fontSize: 9 }}>{r.tier}</span>
                      <span style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--hi)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.file}</span>
                      <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>{r.uses}</span>
                    </div>
                  ))}
              </div>
            </div>
          );
        })() : (
          <div style={{ padding: '16px', color: 'var(--dim)', fontSize: 12 }}>
            <div className="lbl" style={{ marginBottom: 8 }}>Inspector</div>
            Select a node to inspect its relations, degree, and decay.
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--hair)' }}>
              <div className="lbl" style={{ marginBottom: 8 }}>Clusters</div>
              {[
                { n: 'Identity & tone', count: 7, color: 'var(--hi)' },
                { n: 'Kontext internals', count: 9, color: 'var(--fg)' },
                { n: 'Projects active', count: 5, color: 'var(--fg)' },
                { n: 'Feedback loop',   count: 5, color: 'var(--dim)' },
                { n: 'Reference / long-tail', count: 4, color: 'var(--dim)' },
              ].map(c => (
                <div key={c.n} style={{ display: 'grid', gridTemplateColumns: '12px 1fr auto', gap: 8, padding: '4px 0', fontSize: 11 }}>
                  <span style={{ width: 7, height: 7, background: c.color, alignSelf: 'center' }} />
                  <span style={{ color: 'var(--fg)' }}>{c.n}</span>
                  <span style={{ fontFamily: 'var(--f-mono)', color: 'var(--dim)' }}>{c.count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

window.Relations = Relations;
