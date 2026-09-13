import { useEffect, useState, type ReactNode } from 'react';
import { Check, CheckCircle2, ChevronDown, ChevronRight, KeyRound, LoaderCircle, PlugZap, Plus, Server, Trash2, X, XCircle } from 'lucide-react';
import { api } from '../api';

type Provider = { enabled?: boolean; name?: string; model?: string; base_url?: string; has_key?: boolean; masked_key?: string; api_key?: string; is_free?: boolean; tier_info?: string };
type CustomProvider = Provider & { id: string; name: string; type: 'openai' | 'anthropic' };
type Config = { providers?: Record<string, Provider>; custom_providers?: CustomProvider[]; active_provider?: string; active_model?: string };
type TestState = { state: 'testing' | 'success' | 'error'; message: string };

const FREE_IDS = new Set(['ollama', 'gemini', 'cerebras', 'groq', 'pollinations', 'kilo', 'aihorde']);
const PAID_IDS = new Set(['openrouter', 'openai', 'anthropic']);

function Fields({ provider, onChange, keyless = false }: { provider: Provider; onChange: (patch: Partial<Provider>) => void; keyless?: boolean }) {
  return <div className="provider-fields">
    <label><span>Model</span><input value={provider.model || ''} onChange={(event) => onChange({ model: event.target.value })} /></label>
    <label><span>Base URL</span><input value={provider.base_url || ''} onChange={(event) => onChange({ base_url: event.target.value })} /></label>
    {!keyless && <label><span>API key</span><div className="input-icon"><KeyRound /><input type="password" autoComplete="off" value={provider.api_key || ''} placeholder={provider.has_key ? 'Leave blank to keep saved key' : 'Enter API key'} onChange={(event) => onChange({ api_key: event.target.value })} /></div></label>}
  </div>;
}

function ProviderGroup({ title, description, count, defaultOpen = false, action, children }: { title: string; description: string; count: number; defaultOpen?: boolean; action?: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return <section className="provider-group">
    <div className="provider-group-head">
      <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}><span>{open ? <ChevronDown /> : <ChevronRight />}</span><span><strong>{title}</strong><small>{description}</small></span><b>{count}</b></button>
      {action && <span className="provider-action-wrap" onClick={() => setOpen(true)}>{action}</span>}
    </div>
    {open && <div className="provider-group-body">{children}</div>}
  </section>;
}

function ProviderCard({ id, provider, status, testState, onChange, onTest }: { id: string; provider: Provider; status: string; testState?: TestState; onChange: (patch: Partial<Provider>) => void; onTest: () => void }) {
  const [open, setOpen] = useState(false);
  const keyless = id === 'kilo' || id === 'pollinations';
  return <article className={`provider-card ${open ? 'expanded' : ''}`}>
    <div className="provider-head">
      <button type="button" className="provider-expand" onClick={() => setOpen((value) => !value)} aria-expanded={open}><span>{open ? <ChevronDown /> : <ChevronRight />}</span><span><strong>{provider.name || id}</strong><small>{status}</small></span></button>
      <label className="switch" title={provider.enabled ? 'Disable provider' : 'Enable provider'}><input aria-label={`${provider.enabled ? 'Disable' : 'Enable'} ${provider.name || id}`} type="checkbox" checked={Boolean(provider.enabled)} onChange={(event) => onChange({ enabled: event.target.checked })} /><span /></label>
    </div>
    {open && <div className="provider-body">
      <Fields provider={provider} keyless={keyless} onChange={onChange} />
      <div className="provider-actions">
        {testState && <span className={`connection-state ${testState.state}`}>{testState.state === 'testing' ? <LoaderCircle /> : testState.state === 'success' ? <CheckCircle2 /> : <XCircle />}{testState.message}</span>}
        <button type="button" className="button" onClick={onTest} disabled={testState?.state === 'testing'}><PlugZap />Test connection</button>
      </div>
    </div>}
  </article>;
}

function CustomCard({ provider, testState, onChange, onRemove, onTest }: { provider: CustomProvider; testState?: TestState; onChange: (patch: Partial<CustomProvider>) => void; onRemove: () => void; onTest: () => void }) {
  const [open, setOpen] = useState(true);
  const status = provider.has_key ? `Key saved · ${provider.masked_key}` : 'Bring your own API endpoint';
  return <article className={`provider-card ${open ? 'expanded' : ''}`}>
    <div className="provider-head">
      <button type="button" className="provider-expand" onClick={() => setOpen((value) => !value)} aria-expanded={open}><span>{open ? <ChevronDown /> : <ChevronRight />}</span><span><strong>{provider.name || 'Custom provider'}</strong><small>{status}</small></span></button>
      <div className="heading-actions"><label className="switch"><input aria-label={`Enable ${provider.name}`} type="checkbox" checked={Boolean(provider.enabled)} onChange={(event) => onChange({ enabled: event.target.checked })} /><span /></label><button type="button" className="icon-button" onClick={onRemove} aria-label={`Remove ${provider.name}`}><Trash2 /></button></div>
    </div>
    {open && <div className="provider-body">
      <div className="custom-name"><label><span>Name</span><input aria-label="Provider name" value={provider.name} onChange={(event) => onChange({ name: event.target.value })} /></label><label><span>Protocol</span><select value={provider.type} onChange={(event) => onChange({ type: event.target.value as CustomProvider['type'] })}><option value="openai">OpenAI compatible</option><option value="anthropic">Anthropic compatible</option></select></label></div>
      <Fields provider={provider} onChange={onChange} />
      <div className="provider-actions">
        {testState && <span className={`connection-state ${testState.state}`}>{testState.state === 'testing' ? <LoaderCircle /> : testState.state === 'success' ? <CheckCircle2 /> : <XCircle />}{testState.message}</span>}
        <button type="button" className="button" onClick={onTest} disabled={testState?.state === 'testing' || !provider.base_url}><PlugZap />Test connection</button>
      </div>
    </div>}
  </article>;
}

export function SettingsModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [config, setConfig] = useState<Config>({});
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [tests, setTests] = useState<Record<string, TestState>>({});
  useEffect(() => { api.get<Config>('/api/settings/providers').then(setConfig).catch((reason) => setError(reason.message)); }, []);

  function update(id: string, patch: Partial<Provider>) { setConfig((current) => ({ ...current, providers: { ...current.providers, [id]: { ...current.providers?.[id], ...patch } } })); }
  function updateCustom(id: string, patch: Partial<CustomProvider>) { setConfig((current) => ({ ...current, custom_providers: (current.custom_providers || []).map((item) => item.id === id ? { ...item, ...patch } : item) })); }
  function addCustom() { setConfig((current) => ({ ...current, custom_providers: [...(current.custom_providers || []), { id: crypto.randomUUID().slice(0, 8), name: 'Custom provider', type: 'openai', enabled: true, base_url: '', model: '' }] })); }

  async function test(id: string, provider: Provider, providerType = 'openai') {
    setTests((current) => ({ ...current, [id]: { state: 'testing', message: 'Connecting…' } }));
    try {
      const result = await api.post<{ success?: boolean; error?: string; message?: string; latency_ms?: number }>('/api/settings/providers/test', { provider: id, base_url: provider.base_url, api_key: provider.api_key, model: provider.model, provider_type: providerType });
      if (!result.success) throw new Error(result.error || 'Connection failed');
      setTests((current) => ({ ...current, [id]: { state: 'success', message: result.message || `Connected in ${result.latency_ms}ms` } }));
    } catch (reason) {
      setTests((current) => ({ ...current, [id]: { state: 'error', message: (reason as Error).message } }));
    }
  }

  async function save() {
    setError('');
    try {
      const result = await api.post<{ config: Config }>('/api/settings/providers', config);
      if (result.config) setConfig(result.config);
      onSaved();
      setSaved(true); window.setTimeout(() => setSaved(false), 1800);
    } catch (reason) { setError((reason as Error).message); }
  }

  const entries = Object.entries(config.providers || {}).filter(([id]) => id !== 'github_models');
  const free = entries.filter(([id]) => FREE_IDS.has(id));
  const paid = entries.filter(([id]) => PAID_IDS.has(id));
  const other = entries.filter(([id]) => !FREE_IDS.has(id) && !PAID_IDS.has(id));
  const renderProvider = ([id, provider]: [string, Provider]) => {
    const keyless = id === 'kilo' || id === 'pollinations';
    const status = provider.has_key ? `Key saved · ${provider.masked_key}` : id === 'ollama' ? 'Local runtime' : keyless ? 'Keyless free cloud access' : provider.tier_info || (provider.is_free ? 'Free tier · add your API key' : 'Add your API key');
    return <ProviderCard key={id} id={id} provider={provider} status={status} testState={tests[id]} onChange={(patch) => update(id, patch)} onTest={() => void test(id, provider, id === 'anthropic' ? 'anthropic' : 'openai')} />;
  };

  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
    <header><div><Server /><div><h2 id="settings-title">Model providers</h2><p>Use free cloud models, paid APIs, or your own OpenAI/Anthropic-compatible endpoint.</p></div></div><button className="icon-button" onClick={onClose} aria-label="Close settings"><X /></button></header>
    <div className="provider-list">
      <ProviderGroup title="Free cloud and local" description="Keyless routes, free-tier APIs, and Ollama" count={free.length} defaultOpen>{free.map(renderProvider)}</ProviderGroup>
      <ProviderGroup title="Paid APIs" description="Connect accounts billed by the provider" count={paid.length}>{paid.map(renderProvider)}</ProviderGroup>
      {other.length > 0 && <ProviderGroup title="Other providers" description="Additional configured connectors" count={other.length}>{other.map(renderProvider)}</ProviderGroup>}
      <ProviderGroup title="Custom and third-party APIs" description="Bring any OpenAI- or Anthropic-compatible API" count={(config.custom_providers || []).length} defaultOpen action={<button type="button" className="button add-provider" onClick={addCustom}><Plus />Add API</button>}>
        {(config.custom_providers || []).map((provider) => <CustomCard key={provider.id} provider={provider} testState={tests[`custom_${provider.id}`]} onChange={(patch) => updateCustom(provider.id, patch)} onRemove={() => setConfig((current) => ({ ...current, custom_providers: (current.custom_providers || []).filter((item) => item.id !== provider.id) }))} onTest={() => void test(`custom_${provider.id}`, provider, provider.type)} />)}
        {!(config.custom_providers || []).length && <div className="custom-empty"><PlugZap /><span><strong>No custom APIs connected</strong><small>Add your provider URL, model, and API key. Compatible services appear in the agent model picker after saving.</small></span></div>}
      </ProviderGroup>
    </div>
    {error && <div className="error-banner">{error}</div>}<footer><span>{saved && <><Check />Saved · models refreshed</>}</span><button className="button" onClick={onClose}>Close</button><button className="button primary" onClick={() => void save()}>Save settings</button></footer>
  </section></div>;
}
