// Settings — single panel, currently: Anthropic API key. Site is behind
// Pangolin SSO so no in-app auth. POSTs via fetch.

function Settings() {
  const [loading, setLoading] = useState(true);
  const [keySet, setKeySet] = useState(false);
  const [masked, setMasked] = useState('');
  const [input, setInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch('/api/config', { credentials: 'same-origin' });
      const d = await r.json();
      setKeySet(!!d.anthropic_api_key_set);
      setMasked(d.anthropic_api_key_masked || '');
    } catch (e) {
      setMessage({ kind: 'error', text: 'failed to load: ' + e.message });
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const save = async (clear = false) => {
    setSaving(true);
    setMessage(null);
    try {
      const r = await fetch('/api/config', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ anthropic_api_key: clear ? '' : input.trim() }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || r.statusText);
      setKeySet(!!d.anthropic_api_key_set);
      setMasked(d.anthropic_api_key_masked || '');
      setInput('');
      setMessage({ kind: 'ok', text: clear ? 'API key removed' : 'API key saved · dashboard synthesis now live' });
    } catch (e) {
      setMessage({ kind: 'error', text: e.message });
    }
    setSaving(false);
  };

  const onSubmit = (e) => { e.preventDefault(); if (input.trim()) save(false); };

  return (
    <div style={{ padding: '40px 56px', maxWidth: 720, overflow: 'auto', height: '100%' }}>
      <div style={{ marginBottom: 32 }}>
        <h1 style={{ fontSize: 20, fontWeight: 500, color: 'var(--hi)', margin: 0, letterSpacing: '-0.01em' }}>
          Settings
        </h1>
        <div className="dim" style={{ fontSize: 12, marginTop: 6 }}>
          Integrations and keys. Gated behind Pangolin SSO — safe to paste credentials.
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 20 }}>
        <header>
          <span className="title">Anthropic · Claude Haiku synthesis</span>
          <span className="meta">{keySet ? 'enabled' : 'disabled'}</span>
        </header>
        <div className="body">
          <div className="dim" style={{ fontSize: 12, marginBottom: 14, lineHeight: 1.6 }}>
            Powers the <span className="mono" style={{ color: 'var(--fg)' }}>why</span> and{' '}
            <span className="mono" style={{ color: 'var(--fg)' }}>body</span> fields on every entry.
            Without a key, entries fall back to <span className="mono" style={{ color: 'var(--fg)' }}>"N facts captured"</span>.
            Cost at this library size: ~$0.001 per dashboard rebuild.
          </div>

          {loading ? (
            <div className="dim" style={{ fontSize: 12 }}>loading…</div>
          ) : (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, fontSize: 12 }}>
                <span className="lbl">current</span>
                <span className="mono" style={{ color: keySet ? 'var(--accent)' : 'var(--dim)' }}>
                  {masked || 'not set'}
                </span>
              </div>

              <form onSubmit={onSubmit}>
                <div className="input" style={{ marginBottom: 10 }}>
                  <input
                    type="password"
                    autoComplete="off"
                    spellCheck="false"
                    placeholder="sk-ant-api03-…"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    style={{ fontFamily: 'var(--f-mono)', fontSize: 12 }}
                  />
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    type="submit"
                    className="btn accent"
                    disabled={saving || !input.trim()}
                    style={{ opacity: (saving || !input.trim()) ? 0.4 : 1 }}
                  >
                    {saving ? 'saving…' : 'Save key'}
                  </button>
                  {keySet && (
                    <button
                      type="button"
                      className="btn ghost"
                      disabled={saving}
                      onClick={() => { if (confirm('Remove the current API key?')) save(true); }}
                      style={{ color: 'var(--red)' }}
                    >
                      Remove
                    </button>
                  )}
                </div>
              </form>
            </>
          )}

          {message && (
            <div
              className="mono"
              style={{
                marginTop: 14,
                fontSize: 11,
                color: message.kind === 'error' ? 'var(--red)' : 'var(--accent)',
              }}
            >
              {message.text}
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <header>
          <span className="title">Storage</span>
          <span className="meta">local</span>
        </header>
        <div className="body" style={{ fontSize: 12 }}>
          <div className="dim" style={{ marginBottom: 6 }}>
            Keys are stored at <span className="mono" style={{ color: 'var(--fg)' }}>/app/data/dashboard_config.json</span> inside the kontext container, on the same volume as the SQLite DB. Survives container restarts; deleted with the data volume.
          </div>
        </div>
      </div>
    </div>
  );
}
