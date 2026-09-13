import { CSSProperties, lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { Bot, Command, FileCode2, FolderOpen, GitBranch, History, PanelBottom, PanelRightOpen, Search, Settings2, TerminalSquare } from 'lucide-react';
import { api, languageForPath, Workspace } from './api';
import { ChatPanel } from './components/ChatPanel';
import { BrandIcon } from './components/BrandIcon';
import type { EditorTab } from './components/EditorPane';
import { Explorer } from './components/Explorer';
import { GitPanel } from './components/GitPanel';
import { SettingsModal } from './components/SettingsModal';
import { TerminalPane } from './components/TerminalPane';

type View = 'explorer' | 'search' | 'git' | 'history';
const EditorPane = lazy(() => import('./components/EditorPane').then((module) => ({ default: module.EditorPane })));

export function App() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [view, setView] = useState<View>('explorer');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(238);
  const [tabs, setTabs] = useState<EditorTab[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(true);
  const [chatWidth, setChatWidth] = useState(418);
  const [resizing, setResizing] = useState<'sidebar' | 'chat' | null>(null);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [terminalCommand, setTerminalCommand] = useState<{ id: number; text: string }>();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [requestedSession, setRequestedSession] = useState<string>();
  const [version, setVersion] = useState(0);
  const [modelVersion, setModelVersion] = useState(0);
  const [notice, setNotice] = useState('');

  const reloadWorkspace = useCallback(() => api.get<Workspace>('/api/workspace').then(setWorkspace), []);
  useEffect(() => { void reloadWorkspace(); }, [reloadWorkspace]);

  function selectView(next: View) {
    if (sidebarOpen && view === next) setSidebarOpen(false);
    else { setView(next); setSidebarOpen(true); }
  }

  useEffect(() => {
    if (!resizing) return;
    const move = (event: PointerEvent) => {
      if (resizing === 'sidebar') setSidebarWidth(Math.min(430, Math.max(180, event.clientX - 45)));
      else setChatWidth(Math.min(720, Math.max(320, window.innerWidth - event.clientX)));
    };
    const stop = () => setResizing(null);
    document.body.classList.add('resizing-panels');
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop);
    return () => { document.body.classList.remove('resizing-panels'); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); };
  }, [resizing]);

  async function chooseWorkspace() {
    let selected: string | undefined;
    if (window.desktopApp) {
      const result = await window.desktopApp.openDirectory(); selected = result.path;
    } else {
      const result = await api.post<Workspace & { cancelled?: boolean }>('/api/workspace/pick-native');
      if (!result.cancelled) { setWorkspace(result); setTabs([]); setActivePath(null); return; }
    }
    if (selected) { setWorkspace(await api.post('/api/workspace', { path: selected })); setTabs([]); setActivePath(null); }
  }

  async function openFile(path: string) {
    const existing = tabs.find((tab) => tab.path === path);
    if (existing) return setActivePath(path);
    try {
      const data = await api.get<{ content: string; path: string; sha256: string }>(`/api/fs/read?path=${encodeURIComponent(path)}`);
      setTabs((items) => [...items, { path: data.path, name: data.path.split(/[\\/]/).pop() || data.path, content: data.content, saved: data.content, sha256: data.sha256 }]); setActivePath(data.path);
    } catch (error) { setNotice((error as Error).message); }
  }
  function closeTab(path: string) {
    const tab = tabs.find((item) => item.path === path);
    if (tab && tab.content !== tab.saved && !window.confirm(`Close ${tab.name} without saving?`)) return;
    const index = tabs.findIndex((item) => item.path === path);
    const next = tabs.filter((item) => item.path !== path); setTabs(next);
    if (activePath === path) setActivePath(next[Math.min(index, next.length - 1)]?.path || null);
  }
  async function saveFile(path: string) {
    const tab = tabs.find((item) => item.path === path); if (!tab) return;
    try {
      const saved = await api.post<{ sha256: string }>('/api/fs/write', { path, content: tab.content, expected_sha256: tab.sha256 });
      setTabs((items) => items.map((item) => item.path === path ? { ...item, saved: item.content, sha256: saved.sha256 } : item)); setVersion((v) => v + 1); setNotice(`Saved ${tab.name}`);
    } catch (error) { setNotice((error as Error).message); }
  }
  async function reloadFile(path?: string) {
    setVersion((value) => value + 1);
    if (!path) return;
    const tab = tabs.find((item) => item.path === path); if (!tab || tab.content !== tab.saved) return;
    const data = await api.get<{ content: string; sha256: string }>(`/api/fs/read?path=${encodeURIComponent(path)}`).catch(() => null);
    if (data) setTabs((items) => items.map((item) => item.path === path ? { ...item, content: data.content, saved: data.content, sha256: data.sha256 } : item));
  }
  function handleRenamed(oldPath: string, newPath: string) {
    const remap = (path: string) => path === oldPath || path.startsWith(`${oldPath}/`) || path.startsWith(`${oldPath}\\`) ? `${newPath}${path.slice(oldPath.length)}` : path;
    setTabs((items) => items.map((item) => {
      const path = remap(item.path);
      return path === item.path ? item : { ...item, path, name: path.split(/[\\/]/).pop() || path };
    }));
    setActivePath((path) => path ? remap(path) : null);
    setVersion((value) => value + 1);
  }
  function handleDeleted(path: string) {
    const removed = (candidate: string) => candidate === path || candidate.startsWith(`${path}/`) || candidate.startsWith(`${path}\\`);
    const remaining = tabs.filter((item) => !removed(item.path));
    setTabs(remaining);
    if (activePath && removed(activePath)) setActivePath(remaining.at(-1)?.path || null);
    setVersion((value) => value + 1);
  }
  async function runFile(path: string) {
    const quote = `'${path.replaceAll("'", "'\\''")}'`;
    const extension = path.split('.').pop()?.toLowerCase();
    const runner = extension === 'py' ? 'python3' : extension === 'js' ? 'node' : extension === 'sh' ? 'bash' : '';
    if (!runner) { setNotice('Run is supported for Python, JavaScript, and shell files.'); return; }
    setTerminalOpen(true);
    setTerminalCommand({ id: Date.now(), text: `${runner} -- ${quote}` });
  }

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (activePath) void saveFile(activePath); }
      if ((event.ctrlKey || event.metaKey) && event.key === '`') { event.preventDefault(); setTerminalOpen((value) => !value); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'l') { event.preventDefault(); setChatOpen(true); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setView('search'); setSidebarOpen(true); }
    };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  }, [activePath, tabs]);

  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(''), 2600); return () => window.clearTimeout(timer); }, [notice]);
  const active = useMemo(() => tabs.find((tab) => tab.path === activePath), [tabs, activePath]);

  return <div className="app-shell">
    <header className="titlebar"><div className="brand"><BrandIcon className="brand-mark" /><strong>CodeMate</strong></div><button className="workspace-button" onClick={chooseWorkspace}><FolderOpen /><span>{workspace?.has_workspace ? workspace.name : 'Open workspace'}</span></button><button className="command-button" onClick={() => { setView('search'); setSidebarOpen(true); }}><Search /><span>Find in workspace</span><kbd>Ctrl K</kbd></button><div className="title-actions"><button className="icon-button" onClick={() => setTerminalOpen(!terminalOpen)} title="Toggle terminal" aria-label="Toggle terminal"><PanelBottom /></button><button className="icon-button" onClick={() => setChatOpen(!chatOpen)} title="Toggle agent" aria-label="Toggle agent"><PanelRightOpen /></button><button className="icon-button" onClick={() => setSettingsOpen(true)} title="Settings" aria-label="Settings"><Settings2 /></button></div></header>
    <div className={`workbench ${sidebarOpen ? '' : 'sidebar-collapsed'} ${chatOpen ? '' : 'chat-is-collapsed'}`} style={{ '--sidebar-width': `${sidebarWidth}px`, '--chat-width': `${chatWidth}px` } as CSSProperties}>
      <nav className="activity-bar" aria-label="Primary navigation">
        <div><button className={sidebarOpen && view === 'explorer' ? 'active' : ''} onClick={() => selectView('explorer')} title="Explorer" aria-label="Explorer"><FileCode2 /></button><button className={sidebarOpen && view === 'search' ? 'active' : ''} onClick={() => selectView('search')} title="Search" aria-label="Search"><Search /></button><button className={sidebarOpen && view === 'git' ? 'active' : ''} onClick={() => selectView('git')} title="Source control" aria-label="Source control"><GitBranch /></button><button className={sidebarOpen && view === 'history' ? 'active' : ''} onClick={() => selectView('history')} title="History" aria-label="History"><History /></button></div><div><button onClick={() => setTerminalOpen(!terminalOpen)} title="Terminal" aria-label="Terminal"><TerminalSquare /></button><button className={chatOpen ? 'active' : ''} onClick={() => setChatOpen(!chatOpen)} title="Agent" aria-label="Agent"><Bot /></button></div>
      </nav>
      <aside className="left-sidebar" aria-hidden={!sidebarOpen}>
        {view === 'explorer' || view === 'search' ? <Explorer mode={view} root={workspace?.current_path || null} activePath={activePath} version={version} onOpen={openFile} onChanged={() => setVersion((v) => v + 1)} onRenamed={handleRenamed} onDeleted={handleDeleted} /> : view === 'git' ? <GitPanel root={workspace?.current_path || null} version={version} onOpen={openFile} /> : <HistoryPanel onLoad={(id) => { setRequestedSession(id); setChatOpen(true); setNotice(`Loaded session ${id.slice(0, 10)}`); }} />}
        <div className="panel-resizer sidebar-resizer" role="separator" aria-label="Resize sidebar" aria-orientation="vertical" tabIndex={0} onPointerDown={() => setResizing('sidebar')} onKeyDown={(event) => { if (event.key === 'ArrowLeft') setSidebarWidth((value) => Math.max(180, value - 16)); if (event.key === 'ArrowRight') setSidebarWidth((value) => Math.min(430, value + 16)); }} />
      </aside>
      <div className="center-stack"><Suspense fallback={<div className="editor-empty">Loading editor…</div>}><EditorPane tabs={tabs} activePath={activePath} onActivate={setActivePath} onChange={(path, content) => setTabs((items) => items.map((item) => item.path === path ? { ...item, content } : item))} onClose={closeTab} onSave={saveFile} onRun={runFile} /></Suspense>{terminalOpen && <TerminalPane cwd={workspace?.current_path || null} onClose={() => setTerminalOpen(false)} commandRequest={terminalCommand} />}</div>
      <ChatPanel collapsed={!chatOpen} workspace={workspace?.current_path || null} activeFile={active ? { path: active.path, name: active.name, content: active.content, language: languageForPath(active.path) } : undefined} onFileUpdated={reloadFile} onOpenSettings={() => setSettingsOpen(true)} onCollapse={() => setChatOpen(false)} onExpand={() => setChatOpen(true)} onResizeStart={() => setResizing('chat')} onResize={(delta) => setChatWidth((value) => Math.min(720, Math.max(320, value + delta)))} requestedSession={requestedSession} modelVersion={modelVersion} />
    </div>
    <footer className="statusbar"><span><GitBranch />{workspace?.has_workspace ? workspace.short_path : 'No workspace'}</span><span className="status-spacer" /><span><Command />UTF-8</span><span>{active ? languageForPath(active.path) : 'CodeMate'}</span></footer>
    {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} onSaved={() => setModelVersion((value) => value + 1)} />}{notice && <div className="toast">{notice}</div>}
  </div>;
}

function HistoryPanel({ onLoad }: { onLoad: (id: string) => void }) {
  const [items, setItems] = useState<Array<{ id: string; title?: string; updated_at?: string }>>([]);
  useEffect(() => { api.get<Array<{ id: string; title?: string; updated_at?: string }>>('/api/history').then(setItems).catch(() => undefined); }, []);
  function formatTime(value?: string) {
    if (!value) return '';
    const numeric = Number(value);
    const date = Number.isFinite(numeric) ? new Date(numeric < 1e12 ? numeric * 1000 : numeric) : new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }
  return <section className="sidebar-section"><div className="section-heading"><strong>Agent history</strong></div><div className="history-list">{items.map((item) => <button key={item.id} onClick={() => onLoad(item.id)}><History /><span><b>{item.title || 'Untitled session'}</b><small>{formatTime(item.updated_at)}</small></span></button>)}{!items.length && <div className="empty-small">Completed agent sessions appear here.</div>}</div></section>;
}
