// Shared primitives for Kontext dashboard
// Icons are hand-rolled 12px strokes at 1.25 — no icon library.

const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect } = React;

// ---------- Time helpers ----------
const NOW = window.KONTEXT_DATA.now;

function relTime(ts) {
  const diff = NOW - ts;
  const s = Math.round(diff / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.round(h / 24);
  return `${d}d`;
}
function hhmm(ts) {
  const d = new Date(ts);
  return `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}
function fmtNum(n) {
  if (n == null) return '—';
  if (n >= 10000) return (n/1000).toFixed(1) + 'k';
  return String(n);
}

// ---------- Icons ----------
const Icon = ({ d, size = 12 }) => (
  <svg className="ic" viewBox="0 0 12 12" width={size} height={size} aria-hidden>
    <path d={d} />
  </svg>
);
const Icons = {
  search: 'M5 1a4 4 0 1 1 0 8 4 4 0 0 1 0-8zm3 7l3 3',
  arrow:  'M3 6h6m-2-2 2 2-2 2',
  close:  'M2 2l8 8M10 2l-8 8',
  check:  'M2 6l3 3 5-6',
  plus:   'M6 2v8M2 6h8',
  dot:    'M6 5.5v1',
  chev:   'M4 2l3 4-3 4',
  filter: 'M1 2h10M3 6h6M5 10h2',
  graph:  'M3 3a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zM9 7a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zM9 2a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zM4 4.5l4-1M4.3 6l3.4 2',
  decay:  'M2 10c1 0 2-1 2-3s1-3 2-3 2 1 2 3 1 3 2 3',
  overview:'M2 3h3v3H2zM7 3h3v3H7zM2 7h3v3H2zM7 7h3v3H7',
  entries:'M2 2h8v2H2zM2 6h8v2H2zM2 10h5v0',
};

// ---------- Sparkline ----------
function Sparkline({ data, height = 28, stroke = 'var(--fg)', fill = null, dots = false, active = null }) {
  if (!data || !data.length) return null;
  const w = 100, h = height;
  const max = Math.max(...data), min = Math.min(...data);
  const range = Math.max(1, max - min);
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / range) * (h - 4) - 2;
    return [x, y];
  });
  const path = pts.map(([x,y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`)).join(' ');
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      {fill && (
        <path d={`${path} L${w},${h} L0,${h} Z`} fill={fill} stroke="none" />
      )}
      <path d={path} fill="none" stroke={stroke} strokeWidth="1" vectorEffect="non-scaling-stroke" />
      {dots && pts.map(([x,y], i) => (
        <circle key={i} cx={x} cy={y} r="0.8" fill={stroke} />
      ))}
      {active != null && pts[active] && (
        <circle cx={pts[active][0]} cy={pts[active][1]} r="1.5" fill={stroke} />
      )}
    </svg>
  );
}

// ---------- Bar strip ----------
function BarStrip({ data, height = 28, color = 'var(--fg)' }) {
  const max = Math.max(...data);
  return (
    <svg className="spark" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none">
      {data.map((v, i) => {
        const bw = 100 / data.length - 0.6;
        const x = (i / data.length) * 100 + 0.3;
        const bh = (v / max) * (height - 2);
        return <rect key={i} x={x} y={height - bh} width={bw} height={bh} fill={color} />;
      })}
    </svg>
  );
}

// ---------- Radar (5 dimensions) ----------
function Radar({ current, previous, size = 260 }) {
  const dims = [
    { k: 'breadth',   label: 'Work & projects' },
    { k: 'depth',     label: 'How you write' },
    { k: 'recency',   label: 'Health & routine' },
    { k: 'longevity', label: 'Reading & taste' },
    { k: 'linkage',   label: 'Tools & setup' },
  ];
  const pad = 90;                        // room for long, two-line labels outside the polygon
  const vb = size + pad * 2;
  const cx = vb / 2, cy = vb / 2;
  const r = size / 2 - 6;
  const angle = (i) => -Math.PI/2 + i * (Math.PI*2 / dims.length);

  const pt = (val, i, rr = r) => {
    const a = angle(i);
    const x = cx + Math.cos(a) * (val/100) * rr;
    const y = cy + Math.sin(a) * (val/100) * rr;
    return [x, y];
  };

  const poly = (vals) => vals.map((v, i) => pt(v, i).join(',')).join(' ');

  const cur = dims.map(d => current[d.k]);
  const prv = dims.map(d => previous[d.k]);

  const rings = [25, 50, 75, 100];

  return (
    <svg width={size + pad*2} height={size + pad*2} viewBox={`0 0 ${vb} ${vb}`} className="radar" style={{ maxWidth: '100%' }}>
      {/* concentric rings */}
      {rings.map((pct, i) => (
        <polygon key={i}
          points={dims.map((_, j) => {
            const a = angle(j);
            return [cx + Math.cos(a) * (pct/100) * r, cy + Math.sin(a) * (pct/100) * r].join(',');
          }).join(' ')}
          fill="none" stroke="var(--hair)" strokeWidth="1"
        />
      ))}
      {/* spokes */}
      {dims.map((_, i) => {
        const a = angle(i);
        return <line key={i} x1={cx} y1={cy} x2={cx + Math.cos(a)*r} y2={cy + Math.sin(a)*r} stroke="var(--hair)" strokeWidth="1" />;
      })}
      {/* previous (shadow) */}
      <polygon points={poly(prv)} fill="color-mix(in oklab, var(--fg) 6%, transparent)" stroke="var(--dim)" strokeWidth="1" strokeDasharray="2 3" />
      {/* current */}
      <polygon points={poly(cur)} fill="color-mix(in oklab, var(--accent) 10%, transparent)" stroke="var(--accent)" strokeWidth="1.25" />
      {/* current dots */}
      {cur.map((v, i) => {
        const [x, y] = pt(v, i);
        return <g key={i}>
          <circle cx={x} cy={y} r="2.5" fill="var(--bg)" stroke="var(--accent)" strokeWidth="1.25" />
        </g>;
      })}
      {/* labels — wrapped to 2 lines, uppercase, with value underneath */}
      {dims.map((d, i) => {
        const [lx, ly] = pt(100, i, r + 22);
        const a = angle(i);
        const cosA = Math.cos(a), sinA = Math.sin(a);
        // horizontal alignment from the angle so labels push outward
        const anchor = Math.abs(cosA) < 0.2 ? 'middle' : (cosA > 0 ? 'start' : 'end');
        const val = current[d.k];
        // split label at first space for two-line wrap if long
        const words = d.label.split(' ');
        let l1 = d.label, l2 = '';
        if (d.label.length > 11 && words.length > 1) {
          const mid = Math.ceil(words.length / 2);
          l1 = words.slice(0, mid).join(' ');
          l2 = words.slice(mid).join(' ');
        }
        // vertical offset: top label sits higher, bottom sits lower
        const yTop = ly + (sinA < -0.5 ? -10 : sinA > 0.5 ? 4 : -4);
        return (
          <g key={d.k}>
            <text x={lx} y={yTop} textAnchor={anchor} fontFamily="var(--f-sans)" fontSize="9" fill="var(--dim)" letterSpacing="1.5" style={{ textTransform: 'uppercase' }}>{l1}</text>
            {l2 && <text x={lx} y={yTop + 11} textAnchor={anchor} fontFamily="var(--f-sans)" fontSize="9" fill="var(--dim)" letterSpacing="1.5" style={{ textTransform: 'uppercase' }}>{l2}</text>}
            <text x={lx} y={yTop + (l2 ? 24 : 13)} textAnchor={anchor} fontFamily="var(--f-mono)" fontSize="12" fill="var(--hi)">{val}</text>
          </g>
        );
      })}
    </svg>
  );
}

// ---------- Meter (horizontal) ----------
function Meter({ value, max = 100, label, delta }) {
  const pct = (value / max) * 100;
  return (
    <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--hair)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <span className="lbl">{label}</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--hi)' }}>
          {value}<span style={{ color: 'var(--dim)' }}>/{max}</span>
          {delta != null && <span style={{ marginLeft: 8, color: delta >= 0 ? 'var(--accent)' : 'var(--dim)' }}>{delta >= 0 ? '+' : ''}{delta}</span>}
        </span>
      </div>
      <div style={{ position: 'relative', height: 3, background: 'var(--hair)' }}>
        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${pct}%`, background: 'var(--fg)' }} />
        {/* ticks */}
        {[25, 50, 75].map(t => (
          <div key={t} style={{ position: 'absolute', left: `${t}%`, top: -1, bottom: -1, width: 1, background: 'var(--bg)' }} />
        ))}
      </div>
    </div>
  );
}

// ---------- Key bindings ----------
function useKey(keys, handler, deps = []) {
  useEffect(() => {
    const fn = (e) => {
      const tag = (e.target.tagName || '').toLowerCase();
      const editing = tag === 'input' || tag === 'textarea' || e.target.isContentEditable;
      const key = (e.metaKey || e.ctrlKey ? 'mod+' : '') + e.key.toLowerCase();
      if (keys.includes(key) || keys.includes(e.key)) {
        const shouldIgnoreEditing = !keys.includes('mod+k') && editing;
        if (shouldIgnoreEditing) return;
        handler(e);
      }
    };
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, deps);
}

// ---------- Expose ----------
Object.assign(window, {
  Icon, Icons,
  Sparkline, BarStrip, Radar, Meter,
  relTime, hhmm, fmtNum, NOW,
  useKey,
  useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect,
});
