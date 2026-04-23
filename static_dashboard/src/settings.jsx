// Settings — API key, base URL, model. Site gated by Pangolin SSO,
// so these endpoints are implicitly authenticated.

function Settings() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  // Server state
  const [keySet, setKeySet] = useState(false);
  const [masked, setMasked] = useState('');
  const [savedBaseUrl, setSavedBaseUrl] = useState('');
  const [savedModel, setSavedModel] = useState('');
  const [defaultModel, setDefaultModel] = useState('claude-haiku-4-5');

  // Form state
  const [keyInput, setKeyInput] = useState('');
  const [baseUrlInput, setBaseUrlInput] = useState('');
  const [modelInput, setModelInput] = useState('');

  const applyResponse = (d) => {
    setKeySet(!!d.anthropic_api_key_set);
    setMasked(d.anthropic_api_key_masked || '');
    setSavedBaseUrl(d.anthropic_base_url || '');
    setSavedModel(d.anthropic_model || '');
    setDefaultModel(d.anthropic_model_default || 'claude-haiku-4-5');
    setBaseUrlInput(d.anthropic_base_url || '');
    setModelInput(d.anthropic_model || '');
  };

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch('/api/config', { credentials: 'same-origin' });
      applyResponse(await r.json());
    } catch (e) {
      setMessage({ kind: 'error', text: 'failed to load: ' + e.message });
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const save = async (payload, successMsg) => {
    setSaving(true);
    setMessage(null);
    try {
      const r = await fetch('/api/config', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || r.statusText);
      applyResponse(d);
      setKeyInput('');
      setMessage({ kind: 'ok', text: successMsg });
    } catch (e) {
      setMessage({ kind: 'error', text: e.message });
    }
    setSaving(false);
  };

  const onSaveKey = (e) => { e.preventDefault(); if (keyInput.trim()) save({ anthropic_api_key: keyInput.trim() }, 'API key saved'); };
  const onRemoveKey = () => { if (confirm('Remove the current API key?')) save({ anthropic_api_key: '' }, 'API key removed'); };

  const onSaveEndpoint = (e) => {
    e.preventDefault();
    save(
      { anthropic_base_url: baseUrlInput.trim(), anthropic_model: modelInput.trim() || defaultModel },
      'Endpoint + model saved',
    );
  };
  const onResetEndpoint = () => {
    save(
      { anthropic_base_url: '', anthropic_model: '' },
      'Endpoint + model reset to defaults',
    );
  };

  return (
    <div style={{ padding: '40px 56px', maxWidth: 760, overflow: 'auto', height: '100%' }}>
      <div style={{ marginBottom: 32 }}>
        <h1 style={{ fontSize: 20, fontWeight: 500, color: 'var(--hi)', margin: 0, letterSpacing: '-0.01em' }}>
          Settings
        </h1>
        <div className="dim" style={{ fontSize: 12, marginTop: 6 }}>
          Integrations and keys. Gated behind Pangolin SSO — safe to paste credentials.
        </div>
      </div>

      {/* KEY */}
      <div className="panel" style={{ marginBottom: 20 }}>
        <header>
          <span className="title">Anthropic · API key</span>
          <span className="meta">{keySet ? 'set' : 'missing'}</span>
        </header>
        <div className="body">
          <div className="dim" style={{ fontSize: 12, marginBottom: 14, lineHeight: 1.6 }}>
            Powers dashboard entry synthesis (<span className="mono" style={{ color: 'var(--fg)' }}>why</span> + <span className="mono" style={{ color: 'var(--fg)' }}>body</span>). Works with both the official Anthropic API and any compatible proxy (OmniRoute, LiteLLM, etc.) configured below.
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

              <form onSubmit={onSaveKey}>
                <div className="input" style={{ marginBottom: 10 }}>
                  <input
                    type="password" autoComplete="off" spellCheck="false"
                    placeholder="sk-ant-… or your OmniRoute token"
                    value={keyInput}
                    onChange={(e) => setKeyInput(e.target.value)}
                    style={{ fontFamily: 'var(--f-mono)', fontSize: 12 }}
                  />
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button type="submit" className="btn accent"
                    disabled={saving || !keyInput.trim()}
                    style={{ opacity: (saving || !keyInput.trim()) ? 0.4 : 1 }}>
                    {saving ? 'saving…' : 'Save key'}
                  </button>
                  {keySet && (
                    <button type="button" className="btn ghost" disabled={saving}
                      onClick={onRemoveKey} style={{ color: 'var(--red)' }}>
                      Remove
                    </button>
                  )}
                </div>
              </form>
            </>
          )}
        </div>
      </div>

      {/* ENDPOINT + MODEL */}
      <div className="panel" style={{ marginBottom: 20 }}>
        <header>
          <span className="title">Endpoint + model</span>
          <span className="meta">{savedBaseUrl ? 'custom' : 'anthropic default'}</span>
        </header>
        <div className="body">
          <div className="dim" style={{ fontSize: 12, marginBottom: 14, lineHeight: 1.6 }}>
            Leave base URL blank to call Anthropic directly (<span className="mono" style={{ color: 'var(--fg)' }}>https://api.anthropic.com</span>). Set it to your proxy for OmniRoute / LiteLLM etc. Model names may differ between providers — OmniRoute typically uses prefixed IDs like <span className="mono" style={{ color: 'var(--fg)' }}>cc/claude-haiku-4-5</span>.
          </div>

          {!loading && (
            <form onSubmit={onSaveEndpoint}>
              <div style={{ marginBottom: 12 }}>
                <div className="lbl" style={{ marginBottom: 5 }}>Base URL</div>
                <div className="input">
                  <input
                    type="text" autoComplete="off" spellCheck="false"
                    placeholder="https://omniroute.ionutrosu.xyz/v1"
                    value={baseUrlInput}
                    onChange={(e) => setBaseUrlInput(e.target.value)}
                    style={{ fontFamily: 'var(--f-mono)', fontSize: 12 }}
                  />
                </div>
              </div>

              <div style={{ marginBottom: 14 }}>
                <div className="lbl" style={{ marginBottom: 5 }}>Model</div>
                <div className="input">
                  <input
                    type="text" autoComplete="off" spellCheck="false"
                    placeholder={defaultModel}
                    value={modelInput}
                    onChange={(e) => setModelInput(e.target.value)}
                    style={{ fontFamily: 'var(--f-mono)', fontSize: 12 }}
                  />
                </div>
                <div className="dim mono" style={{ fontSize: 10, marginTop: 4 }}>
                  default · {defaultModel}
                </div>
              </div>

              <div style={{ display: 'flex', gap: 8 }}>
                <button type="submit" className="btn accent" disabled={saving}
                  style={{ opacity: saving ? 0.4 : 1 }}>
                  {saving ? 'saving…' : 'Save endpoint + model'}
                </button>
                {(savedBaseUrl || savedModel) && (
                  <button type="button" className="btn ghost" disabled={saving}
                    onClick={onResetEndpoint}>
                    Reset to defaults
                  </button>
                )}
              </div>
            </form>
          )}
        </div>
      </div>

      {message && (
        <div className="mono" style={{
          fontSize: 11,
          color: message.kind === 'error' ? 'var(--red)' : 'var(--accent)',
          marginBottom: 20,
        }}>
          {message.text}
        </div>
      )}

      <div className="panel">
        <header>
          <span className="title">Storage</span>
          <span className="meta">local</span>
        </header>
        <div className="body" style={{ fontSize: 12 }}>
          <div className="dim" style={{ marginBottom: 6 }}>
            Config lives at <span className="mono" style={{ color: 'var(--fg)' }}>/app/data/dashboard_config.json</span> on the kontext container's data volume. Survives restarts; deleted with the volume.
          </div>
        </div>
      </div>
    </div>
  );
}
