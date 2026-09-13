import { FormEvent, useEffect, useRef, useState } from 'react';
import { ArrowDown, Bot, BrainCircuit, Check, ChevronDown, ChevronRight, CircleStop, Globe2, PanelRightClose, Paperclip, Plus, Send, Settings2, ShieldCheck, Sparkles, Wrench, X, Zap } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api, ChatMessage, ModelInfo, ModelsResponse, streamChat } from '../api';
import { ModelPicker } from './ModelPicker';

type RunStep = { step_id: string; layer: string; status: string; label: string; detail?: string };
type Props = {
  workspace: string | null;
  activeFile?: { path: string; name: string; content: string; language: string };
  onFileUpdated: (path?: string) => void;
  onOpenSettings: () => void;
  onCollapse: () => void;
  onExpand: () => void;
  onResizeStart: () => void;
  onResize: (delta: number) => void;
  collapsed: boolean;
  requestedSession?: string;
  modelVersion: number;
};
type ApprovalPolicy = 'ask' | 'workspace' | 'whole_device';
type WebMode = 'off' | 'auto' | 'research';

function Message({ message }: { message: ChatMessage }) {
  return <article className={`message ${message.role}`}>
    <div className="message-role">{message.role === 'assistant' ? <><Bot />VexP</> : 'You'}</div>
    <div className="message-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>
  </article>;
}

export function ChatPanel({ workspace, activeFile, onFileUpdated, onOpenSettings, onCollapse, onExpand, onResizeStart, onResize, collapsed, requestedSession, modelVersion }: Props) {
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [tier, setTier] = useState<'fast' | 'complex'>('fast');
  const [approvalPolicy, setApprovalPolicy] = useState<ApprovalPolicy>(() => {
    const saved = window.localStorage.getItem('vexp-command-access');
    return saved === 'workspace' || saved === 'whole_device' ? saved : 'ask';
  });
  const [webMode, setWebMode] = useState<WebMode>(() => {
    const saved = window.localStorage.getItem('vexp-web-mode');
    return saved === 'off' || saved === 'research' ? saved : 'auto';
  });
  const [localOnly, setLocalOnly] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState('');
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [steps, setSteps] = useState<RunStep[]>([]);
  const [toolLine, setToolLine] = useState('');
  const [routeLine, setRouteLine] = useState('');
  const [runError, setRunError] = useState('');
  const [attachments, setAttachments] = useState<Array<{ name: string; content: string }>>([]);
  const [approval, setApproval] = useState<{ runId: string; callId: string; command: string; accessScope: string } | null>(null);
  const [runExpanded, setRunExpanded] = useState(false);
  const [sessionId, setSessionId] = useState(() => `session_${crypto.randomUUID()}`);
  const controller = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const followOutput = useRef(true);
  const [showJump, setShowJump] = useState(false);

  useEffect(() => {
    api.get<ModelsResponse>('/api/models').then((value) => {
      const initialTier = value.active_tier || (value.active_model === 'complex-auto' ? 'complex' : 'fast');
      const list = initialTier === 'fast' ? value.fast_models || [] : value.complex_models || [];
      const selectedExists = list.some((entry) => entry.provider === value.active_provider && entry.model === value.active_model);
      const selected = selectedExists ? { provider: value.active_provider, model: value.active_model } : list[0];
      setModels(value); setTier(initialTier); setProvider(selected?.provider || 'free_pool'); setModel(selected?.model || 'fast-auto');
    }).catch(() => undefined);
  }, [modelVersion]);
  useEffect(() => { window.localStorage.setItem('vexp-command-access', approvalPolicy); }, [approvalPolicy]);
  useEffect(() => { window.localStorage.setItem('vexp-web-mode', webMode); }, [webMode]);
  useEffect(() => {
    if (!requestedSession) return;
    api.get<{ id: string; messages?: ChatMessage[] }>(`/api/history/${encodeURIComponent(requestedSession)}`).then((session) => {
      followOutput.current = true; setShowJump(false);
      setSessionId(session.id); setMessages(session.messages || []); setSteps([]); setToolLine(''); setRouteLine(''); setRunError('');
    }).catch(() => undefined);
  }, [requestedSession]);
  useEffect(() => {
    if (!followOutput.current) return;
    const frame = window.requestAnimationFrame(() => endRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' }));
    return () => window.cancelAnimationFrame(frame);
  }, [messages, steps, running, toolLine, routeLine, runError, approval]);

  function handleScroll() {
    const element = scrollRef.current;
    if (!element) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 72;
    followOutput.current = atBottom;
    setShowJump(!atBottom);
  }

  function jumpToLatest() {
    followOutput.current = true; setShowJump(false);
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }

  const modelList: ModelInfo[] = tier === 'fast' ? (models?.fast_models || []) : (models?.complex_models || []);
  function selectModel(value: string) {
    const selected = modelList.find((entry) => `${entry.provider}::${entry.model}` === value);
    if (!selected) return;
    setProvider(selected.provider); setModel(selected.model);
    void api.post('/api/models/switch', { provider: selected.provider, model: selected.model, tier });
  }

  function selectTier(nextTier: 'fast' | 'complex') {
    const list = nextTier === 'fast' ? models?.fast_models || [] : models?.complex_models || [];
    const automatic = list.find((entry) => entry.provider === 'free_pool') || list[0];
    setTier(nextTier);
    if (automatic) {
      setProvider(automatic.provider); setModel(automatic.model);
      void api.post('/api/models/switch', { provider: automatic.provider, model: automatic.model, tier: nextTier });
    }
  }

  function selectApprovalPolicy(nextPolicy: ApprovalPolicy) {
    if (nextPolicy === 'whole_device' && approvalPolicy !== 'whole_device' && !window.confirm('Whole device access lets agent commands use your files, installed programs, and network without asking each time. Enable it?')) return;
    setApprovalPolicy(nextPolicy);
  }

  async function send(event?: FormEvent) {
    event?.preventDefault();
    const text = prompt.trim();
    if ((!text && !activeFile) || running) return;
    const currentRun = `run_${crypto.randomUUID().replaceAll('-', '')}`;
    followOutput.current = true; setShowJump(false);
    setRunId(currentRun); setPrompt(''); setRunning(true); setRunExpanded(true); setSteps([]); setToolLine(''); setRouteLine(''); setRunError('');
    setMessages((items) => [...items, { role: 'user', content: text || `Review ${activeFile?.name}` }, { role: 'assistant', content: '' }]);
    controller.current = new AbortController();
    const requestedWeb = /\b(?:browse|google|web\s+search|search\s+(?:the\s+)?web|search\s+online|internet\s+search|look\s+up\s+online|web\s+access|internet\s+access|access\s+(?:the\s+)?(?:web|internet)|search\s+(?:the\s+)?internet|can\s+you\s+(?:browse|search))\b/i.test(text)
      || /\b(?:latest|current|today'?s?)\s+(?:news|price|prices|release|version|schedule|score|scores|weather)\b/i.test(text);
    const useWeb = webMode === 'research' || (webMode === 'auto' && requestedWeb);
    try {
      await streamChat({ session_id: sessionId, run_id: currentRun, prompt: text, project_path: workspace, provider, model, tier, mode: workspace ? 'code' : 'ask', approval_policy: approvalPolicy, web_mode: webMode, web_search: useWeb, local_only: localOnly, active_file: activeFile, attachments }, (data) => {
        const type = String(data.type || '');
        if (type === 'token') setMessages((items) => items.map((item, index) => index === items.length - 1 ? { ...item, content: item.content + String(data.text || '') } : item));
        if (type === 'response_reset') setMessages((items) => items.map((item, index) => index === items.length - 1 ? { ...item, content: '' } : item));
        if (type === 'harness_step') setSteps((items) => [...items.filter((step) => step.step_id !== data.step_id), data as unknown as RunStep].sort((a, b) => a.step_id.localeCompare(b.step_id)));
        if (type === 'tool_started') setToolLine(`Running ${String(data.name || 'tool')}`);
        if (type === 'tool_completed') setToolLine(`${data.status === 'success' ? 'Completed' : 'Failed'} ${String(data.name || 'tool')}: ${String(data.summary || '')}`);
        if (type === 'model_route') setRouteLine(`${String(data.provider || 'provider')}:${String(data.model || 'model')} · pinned for this run`);
        if (type === 'approval_required') setApproval({ runId: String(data.run_id || currentRun), callId: String(data.call_id), command: String(data.command || ''), accessScope: String(data.access_scope || 'whole_device') });
        if (type === 'file_updated') onFileUpdated(String(data.path || ''));
        if (type === 'error') {
          const message = String(data.text || 'Unknown error');
          setRunError(message);
          setMessages((items) => items.map((item, index) => index === items.length - 1 ? { ...item, content: item.content || `Run stopped: ${message}` } : item));
        }
      }, controller.current.signal);
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        setRunError((error as Error).message);
        setMessages((items) => items.map((item, index) => index === items.length - 1 ? { ...item, content: item.content || `Run stopped: ${(error as Error).message}` } : item));
      }
    } finally { setRunning(false); setRunExpanded(false); setAttachments([]); setApproval(null); controller.current = null; }
  }

  async function stop() {
    if (runId) await api.post(`/api/runs/${encodeURIComponent(runId)}/cancel`).catch(() => undefined);
    controller.current?.abort(); setRunning(false);
  }

  async function decideApproval(approved: boolean) {
    if (!approval) return;
    await api.post(`/api/runs/${encodeURIComponent(approval.runId)}/approval`, { call_id: approval.callId, approved }).catch(() => undefined);
    setApproval(null);
  }

  function freshSession() { followOutput.current = true; setShowJump(false); setSessionId(`session_${crypto.randomUUID()}`); setMessages([]); setSteps([]); setToolLine(''); setRouteLine(''); setRunError(''); }

  async function attachFiles(files: FileList | null) {
    if (!files) return;
    for (const file of Array.from(files).slice(0, 8 - attachments.length)) {
      const body = new FormData(); body.append('file', file);
      const response = await fetch('/api/upload', { method: 'POST', credentials: 'same-origin', body });
      if (!response.ok) continue;
      const uploaded = await response.json() as { filename: string; content: string };
      setAttachments((items) => [...items, { name: uploaded.filename, content: uploaded.content }].slice(0, 8));
    }
  }

  const selectedModel = modelList.find((entry) => entry.provider === provider && entry.model === model);
  if (collapsed) return <aside className="chat-panel chat-collapsed" aria-label="Collapsed agent panel">
    <button className="collapsed-agent" onClick={onExpand} title="Expand VexP Agent" aria-label="Expand VexP Agent"><Sparkles /><span>Agent</span></button>
    <button className="collapsed-selection" onClick={onExpand} title={`${tier === 'fast' ? 'Fast reply' : 'Complex work'} · ${selectedModel?.name || model}`} aria-label={`${tier === 'fast' ? 'Fast reply' : 'Complex work'} using ${selectedModel?.name || model}`}>
      {tier === 'fast' ? <Zap /> : <BrainCircuit />}<span>{tier === 'fast' ? 'Fast' : 'Complex'}</span><small>{selectedModel?.name || model || 'Auto'}</small>
    </button>
    <button className="icon-button collapsed-settings" onClick={onOpenSettings} title="Provider settings" aria-label="Provider settings"><Settings2 /></button>
  </aside>;

  return <aside className="chat-panel" aria-label="Agent chat">
    <div className="panel-resizer chat-resizer" role="separator" aria-label="Resize agent panel" aria-orientation="vertical" tabIndex={0} onPointerDown={onResizeStart} onKeyDown={(event) => { if (event.key === 'ArrowLeft') onResize(16); if (event.key === 'ArrowRight') onResize(-16); }} />
    <header className="chat-header"><div><Sparkles /><strong>VexP Agent</strong><span className="status-dot" title="Harness ready" /></div><div><button className="icon-button" onClick={freshSession} title="New chat" aria-label="New chat"><Plus /></button><button className="icon-button" onClick={onOpenSettings} title="Provider settings" aria-label="Provider settings"><Settings2 /></button><button className="icon-button" onClick={onCollapse} title="Collapse agent" aria-label="Collapse agent"><PanelRightClose /></button></div></header>
    <div className="chat-context"><span>File context</span><strong>{activeFile?.name || 'No active file'}</strong></div>
    <div className="chat-scroll-shell"><div className="chat-scroll" ref={scrollRef} onScroll={handleScroll}>
      {!messages.length && <div className="chat-welcome"><div className="agent-glyph"><Sparkles /></div><h2>VexP Code AI Ready</h2><p>Autonomous software engineering across local and free cloud models. Ask questions, request edits, or run terminal commands.</p><div className="trust-row"><ShieldCheck /> Access follows your command policy</div></div>}
      {messages.map((message, index) => <Message key={`${message.role}-${index}`} message={message} />)}
      {(running || steps.length > 0 || runError) && <div className={`run-card ${running ? 'running' : ''} ${runError ? 'failed' : ''} ${runExpanded ? 'expanded' : 'collapsed'}`}>
        <button type="button" className="run-card-title" onClick={() => setRunExpanded((value) => !value)} aria-expanded={runExpanded}><span className="pulse-orb" /><strong>{running ? 'Working' : runError ? 'Run stopped' : 'Run complete'}</strong><small>{running ? 'Agent is processing' : runError || `${steps.filter((step) => step.status === 'done').length} steps`}</small>{running && <span className="thinking-wave"><i /><i /><i /></span>}{runExpanded ? <ChevronDown /> : <ChevronRight />}</button>
        {runExpanded && <div className="run-card-details">{steps.map((step) => <div className={`run-step ${step.status}`} key={step.step_id}><span>{step.status === 'done' ? <Check /> : <span className="step-dot" />}</span><div><b>{step.label}</b>{step.detail && <small>{step.detail}</small>}</div></div>)}
        {routeLine && <div className="route-line"><Bot />{routeLine}</div>}
        {toolLine && <div className="tool-line"><Wrench />{toolLine}</div>}
        {runError && <div className="run-error" role="alert">{runError}</div>}
        {approval && <div className="approval-card"><div><ShieldCheck /><span><b>Command approval</b><small>{approval.accessScope === 'whole_device' ? 'This command can access your device and network.' : 'This command is confined to the workspace.'}</small><code>{approval.command}</code></span></div><div><button className="button" onClick={() => decideApproval(false)}>Deny</button><button className="button primary" onClick={() => decideApproval(true)}>Allow once</button></div></div>}</div>}
      </div>}
      <div ref={endRef} />
    </div>{showJump && <button type="button" className="jump-latest" onClick={jumpToLatest}><ArrowDown />Latest</button>}</div>
    <form className="composer" onSubmit={send}>
      {attachments.length > 0 && <div className="context-row">{attachments.map((item) => <button type="button" className="context-chip" key={item.name} onClick={() => setAttachments((items) => items.filter((candidate) => candidate !== item))}><Paperclip />{item.name}<X /></button>)}</div>}
      <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder={workspace ? 'Ask VexP Agent about your project…' : 'Ask VexP Agent, or open a workspace for file tools…'} rows={2} />
      <div className="composer-controls">
        <div className="composer-left">
          <label className="chip-button attach-button" title="Attach files"><Paperclip />Attach<input className="visually-hidden" type="file" multiple onChange={(event) => { void attachFiles(event.target.files); event.target.value = ''; }} /></label>
          <label className={`web-mode-select ${webMode !== 'off' ? 'active' : ''}`} title={webMode === 'auto' ? 'Agent searches when current or missing information requires it' : webMode === 'research' ? 'Search before every response' : 'Disable web tools'}><Globe2 /><select aria-label="Web access" value={webMode} onChange={(event) => setWebMode(event.target.value as WebMode)}><option value="auto">Web Auto</option><option value="research">Web Research</option><option value="off">Web Off</option></select><ChevronDown /></label>
          <button type="button" className={`chip-button ${localOnly ? 'active' : ''}`} onClick={() => setLocalOnly(!localOnly)}><ShieldCheck />Local</button>
        </div>
        {running ? <button type="button" className="send-button stop" onClick={stop} aria-label="Stop run"><CircleStop /></button> : <button type="submit" className="send-button" disabled={!prompt.trim() && !activeFile} aria-label="Send"><Send /></button>}
      </div>
      <div className="model-strip">
        <label className={`access-select ${approvalPolicy === 'whole_device' ? 'device-access' : ''}`} title={approvalPolicy === 'ask' ? 'Ask before each command; approved commands can access the device and network' : approvalPolicy === 'workspace' ? 'Run commands automatically inside the isolated workspace' : 'Run commands automatically with full user-account filesystem and network access'}><ShieldCheck /><select aria-label="Command access" value={approvalPolicy} onChange={(event) => selectApprovalPolicy(event.target.value as ApprovalPolicy)}><option value="ask">Ask commands</option><option value="workspace">Workspace access</option><option value="whole_device">Whole device access</option></select><ChevronDown /></label>
        <div className="segmented tier-switch"><button type="button" className={tier === 'fast' ? 'active' : ''} onClick={() => selectTier('fast')}>Fast reply</button><button type="button" className={tier === 'complex' ? 'active' : ''} onClick={() => selectTier('complex')}>Complex work</button></div>
        <ModelPicker models={modelList} provider={provider} model={model} onSelect={selectModel} />
      </div>
    </form>
  </aside>;
}
