// Overview — restored structure with hairlines, but calm density.
// Score hero (left) + radar (right) · nudge strip · 5-row capture stream · dimension table.

function Overview() {
  const D = window.KONTEXT_DATA;
  const delta = D.score - D.prevScore;
  const feed = D.feed.slice(0, 5);

  // "Equivalent knowing time" — how long a human friend would need to know you
  // to have the same mental model Kontext has built.
  //
  // Model: we translate library richness into human-equivalent months. A human
  // picks up roughly one meaningful datapoint per interaction, interactions
  // happen weekly-ish, and dense tiers (S/A) compound faster than thin ones.
  //
  //   weightedEntries = 3·S + 2·A + 1·B + 0.4·C
  //   linkageBoost   = 1 + avgRelationsPerEntry / 4         // dense graph = deeper understanding
  //   breadthBoost   = 1 + uniqueFiles / 40                 // coverage of life surface
  //   humanMonths    = weightedEntries · linkageBoost · breadthBoost / 6
  //
  // 6 = target datapoints a close friend absorbs per month of regular contact.
  const tierW = { S: 3, A: 2, B: 1, C: 0.4 };
  const weighted = D.entries.reduce((s, e) => s + (tierW[e.tier] || 0.4), 0);
  const avgRel = D.entries.reduce((s, e) => s + (e.relations?.length || 0), 0) / D.entries.length;
  const uniqueFiles = new Set(D.entries.map(e => e.file)).size;
  const linkageBoost = 1 + avgRel / 4;
  const breadthBoost = 1 + uniqueFiles / 40;
  const humanMonths = (weighted * linkageBoost * breadthBoost) / 6;

  let knownLine;
  if (humanMonths >= 12) {
    const y = humanMonths / 12;
    const whole = Math.floor(y);
    const half = (y - whole) >= 0.5;
    knownLine = {
      big: half ? `${whole}½` : `${whole}`,
      unit: whole === 1 && !half ? 'year' : 'years',
    };
  } else if (humanMonths >= 1) {
    const m = Math.round(humanMonths);
    knownLine = { big: `${m}`, unit: m === 1 ? 'month' : 'months' };
  } else {
    const w = Math.max(1, Math.round(humanMonths * 4.3));
    knownLine = { big: `${w}`, unit: w === 1 ? 'week' : 'weeks' };
  }

  const dims = [
    { k: 'breadth',   label: 'How many sides of you it knows', hint: 'different parts of your life covered' },
    { k: 'depth',     label: 'How well it knows each thing',   hint: 'detail and nuance in the notes' },
    { k: 'recency',   label: 'How up to date it is',           hint: 'fresh notes from the last few days' },
    { k: 'longevity', label: 'How long it has been paying attention', hint: 'old notes that still hold up' },
    { k: 'linkage',   label: 'How well it connects the dots',  hint: 'things it sees as related to each other' },
  ];

  return (
    <div style={{ height: '100%', overflow: 'auto' }} className="mount">
      <div style={{ maxWidth: 1160, margin: '0 auto' }}>

        {/* HERO — framed by hairlines top/bottom, split left/right */}
        <section style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr',
          borderBottom: '1px solid var(--hair)',
        }}>
          {/* Score */}
          <div style={{ padding: '32px 56px 36px', borderRight: '1px solid var(--hair)', display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <span className="lbl">Like a friend you've had for</span>
              <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>
                snapshot {hhmm(NOW)} UTC
              </span>
            </div>

            <div style={{ display: 'flex', alignItems: 'baseline', gap: 18 }}>
              <div style={{
                fontFamily: 'var(--f-mono)', fontWeight: 500,
                fontSize: 156, lineHeight: 0.85, letterSpacing: '-0.05em',
                color: 'var(--hi)', fontVariantNumeric: 'tabular-nums',
              }}>
                {knownLine.big}
              </div>
              <div style={{ fontFamily: 'var(--f-sans)', fontSize: 28, color: 'var(--dim)', paddingBottom: 18, letterSpacing: '-0.01em' }}>
                {knownLine.unit}
              </div>
            </div>
          </div>

          {/* Radar — parts of your life it knows about */}
          <div style={{ padding: '28px 40px 32px', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <span className="lbl">Parts of your life it knows about</span>
              <div style={{ display: 'flex', gap: 14, fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>
                <span><span style={{ display: 'inline-block', width: 10, height: 1, background: 'var(--accent)', verticalAlign: 'middle', marginRight: 6 }}></span>now</span>
                <span><span style={{ display: 'inline-block', width: 10, height: 0, borderTop: '1px dashed var(--dim)', verticalAlign: 'middle', marginRight: 6 }}></span>a week ago</span>
              </div>
            </div>
            <div style={{ flex: 1, display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
              <Radar current={D.dimensions} previous={D.prevDimensions} size={260} />
            </div>
          </div>
        </section>

        {/* RECENT FACTS — what Kontext picked up, in plain sentences */}
        <section style={{ borderBottom: '1px solid var(--hair)', padding: '32px 56px 40px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 20 }}>
            <span className="lbl">Things Kontext learned about you recently</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--accent)' }}>
              <span className="pulse" style={{ width: 5, height: 5, background: 'var(--accent)' }} />
              <span style={{ letterSpacing: '0.12em' }}>LIVE</span>
            </span>
          </div>
          <div>
            {[
              { when: 'just now',     fact: 'You switched your default shell from zsh to fish on the 2026 laptop.',            src: 'dotfiles_migration.md' },
              { when: 'yesterday',    fact: 'You prefer replies under 3 bullets when asking a factual lookup.',                  src: 'feedback_2026_04_17.md' },
              { when: '2 days ago',   fact: 'Your sleep window shifted — you now stop caffeine by 1pm, not 3pm.',                src: 'health_constraints.md' },
              { when: '3 days ago',   fact: 'You started a new project called "Kontext" and it\'s your main focus this quarter.', src: 'project_index.md' },
              { when: '4 days ago',   fact: 'You dropped the book "Designing Data-Intensive Applications" halfway through.',     src: 'reading_list_2026.md' },
              { when: '5 days ago',   fact: 'You decided LiteFS over Turso for sync — conflict rules were the deciding factor.', src: 'decisions_log.md' },
              { when: '6 days ago',   fact: 'You called out a hallucinated package name; fewer guesses on unfamiliar libs.',     src: 'feedback_2026_04_10.md' },
            ].map((row, i) => (
              <div key={i} style={{
                display: 'grid',
                gridTemplateColumns: '110px 1fr 200px',
                gap: 24,
                padding: '14px 0',
                borderTop: i === 0 ? '1px solid var(--hair)' : 'none',
                borderBottom: '1px solid var(--hair)',
                alignItems: 'baseline',
              }}>
                <span style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--dim)', textTransform: 'lowercase' }}>{row.when}</span>
                <span style={{ fontSize: 13, color: 'var(--hi)', lineHeight: 1.55 }}>{row.fact}</span>
                <span style={{ fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--dim)', textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.src}</span>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 20, display: 'flex', justifyContent: 'flex-end' }}>
            <button className="btn ghost" style={{ fontSize: 12 }} onClick={() => window.__nav && window.__nav('entries')}>
              See everything →
            </button>
          </div>
        </section>

        {/* TIPS — friendly, actionable hints */}
        <section style={{ padding: '32px 56px 72px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 16 }}>
            <span className="lbl">A few tips to help Kontext know you better</span>
            <span style={{ fontFamily: 'var(--f-mono)', fontSize: 10, color: 'var(--dim)' }}>3 tips</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 1, background: 'var(--hair)', border: '1px solid var(--hair)' }}>
            {[
              {
                big: '6',
                head: 'loose notes',
                body: 'A few things Kontext learned aren\'t tied to anything else yet. Linking them helps it see the bigger picture.',
                cta: 'Review loose notes',
                to: 'relations',
              },
              {
                big: '4',
                head: 'fading memories',
                body: 'Some older notes haven\'t come up in a while. Take a look — keep what still matters, let the rest go.',
                cta: 'Tidy up',
                to: 'decay',
              },
              {
                big: '1',
                head: 'quick prompt',
                body: 'Tell Kontext one thing about your week it doesn\'t know yet. Small updates compound fast.',
                cta: 'Add a note',
                to: 'entries',
              },
            ].map((t, i) => (
              <div key={i} style={{
                background: 'var(--bg)',
                padding: '20px 22px',
                display: 'flex', flexDirection: 'column', gap: 10,
                minHeight: 180,
              }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                  <span style={{
                    fontFamily: 'var(--f-mono)', fontWeight: 500,
                    fontSize: 32, lineHeight: 1, color: 'var(--hi)',
                    fontVariantNumeric: 'tabular-nums',
                  }}>{t.big}</span>
                  <span style={{ fontSize: 12, color: 'var(--dim)', textTransform: 'lowercase' }}>{t.head}</span>
                </div>
                <div style={{ fontSize: 13, color: 'var(--fg)', lineHeight: 1.55, flex: 1 }}>
                  {t.body}
                </div>
                <button
                  className="btn ghost"
                  style={{ fontSize: 12, alignSelf: 'flex-start', padding: '6px 10px' }}
                  onClick={() => window.__nav && window.__nav(t.to)}
                >
                  {t.cta} →
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

window.Overview = Overview;
