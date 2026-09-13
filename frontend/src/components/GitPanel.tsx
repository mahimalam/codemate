import { useEffect, useState } from 'react';
import { Check, ChevronDown, ChevronRight, DownloadCloud, GitBranch, KeyRound, Link2, Minus, Plus, RefreshCw, RotateCcw, Settings2, Unplug, UploadCloud } from 'lucide-react';
import { api, GitFile, GitStatus } from '../api';
import { GitHubIcon } from './GitHubIcon';

type GitHubStatus = { connected: boolean; login: string; name: string; remote_url: string; github_models_available: boolean; github_models_message: string };
type GitConfig = { username?: string; email?: string; remote_url?: string };
type ActionResult = { success?: boolean; output?: string };

export function GitPanel({ root, version, onOpen }: { root: string | null; version: number; onOpen: (path: string) => void }) {
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [github, setGithub] = useState<GitHubStatus | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [token, setToken] = useState('');
  const [remoteUrl, setRemoteUrl] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  async function refresh() {
    if (!root) { setStatus(null); setGithub(null); return; }
    const [gitStatus, githubStatus, gitConfig] = await Promise.all([
      api.get<GitStatus>(`/api/git/status?path=${encodeURIComponent(root)}`),
      api.get<GitHubStatus>(`/api/github/status?path=${encodeURIComponent(root)}`),
      api.get<GitConfig>(`/api/git/config?path=${encodeURIComponent(root)}`),
    ]);
    setStatus(gitStatus); setGithub(githubStatus);
    setRemoteUrl((value) => value || githubStatus.remote_url || gitConfig.remote_url || '');
    setUsername((value) => value || gitConfig.username || '');
    setEmail((value) => value || gitConfig.email || '');
  }
  useEffect(() => { void refresh().catch((reason) => setNotice({ kind: 'error', text: reason.message })); }, [root, version]);

  async function action(path: string, body: unknown, successText?: string) {
    setBusy(true); setNotice(null);
    try {
      const result = await api.post<ActionResult>(path, body);
      if (result.success === false) throw new Error(result.output || 'Git operation failed');
      setNotice({ kind: 'success', text: result.output || successText || 'Git operation completed.' });
      await refresh();
      return result;
    } catch (reason) {
      setNotice({ kind: 'error', text: (reason as Error).message });
      return null;
    } finally { setBusy(false); }
  }

  async function connect() {
    if (!root || !token.trim()) return;
    setBusy(true); setNotice(null);
    try {
      const result = await api.post<GitHubStatus>('/api/github/connect', { token, remote_url: remoteUrl, username, email, path: root });
      setGithub(result); setToken(''); setConnectionOpen(false);
      setNotice({ kind: 'success', text: `Connected GitHub account @${result.login}.` });
      await refresh();
    } catch (reason) { setNotice({ kind: 'error', text: (reason as Error).message }); }
    finally { setBusy(false); }
  }

  function row(file: GitFile) {
    const separator = root?.includes('\\') ? '\\' : '/';
    return <div className="git-file" key={`${file.file}:${file.staged}`}><button onClick={() => root && onOpen(`${root}${separator}${file.file}`)} title={file.file}><span className="git-status-code">{file.status}</span><span>{file.file}</span></button><div>{file.staged ? <button className="icon-button" onClick={() => void action('/api/git/unstage', { file: file.file, path: root })} title="Unstage" aria-label={`Unstage ${file.file}`}><Minus /></button> : <><button className="icon-button" onClick={() => void action('/api/git/discard', { file: file.file, path: root })} title="Discard" aria-label={`Discard ${file.file}`}><RotateCcw /></button><button className="icon-button" onClick={() => void action('/api/git/stage', { file: file.file, path: root })} title="Stage" aria-label={`Stage ${file.file}`}><Plus /></button></>}</div></div>;
  }

  if (!root) return <div className="empty-small">Open a folder to use source control.</div>;
  return <section className="git-panel">
    <div className="section-heading"><strong>Source Control</strong><button className="icon-button" onClick={() => void refresh()} aria-label="Refresh source control" title="Refresh"><RefreshCw /></button></div>
    <div className="github-card">
      <button className="github-summary" onClick={() => setConnectionOpen((value) => !value)} aria-expanded={connectionOpen}><GitHubIcon /><span><strong>{github?.connected ? `@${github.login}` : 'Connect GitHub'}</strong><small>{github?.connected ? 'Authenticated for repository operations' : 'Push, pull, and manage the current remote'}</small></span>{connectionOpen ? <ChevronDown /> : <ChevronRight />}</button>
      {connectionOpen && <div className="github-connect-body">
        {github?.connected ? <>
          <div className="github-account"><Check /><span><strong>{github.name || github.login}</strong><small>{github.remote_url || 'No origin remote configured'}</small></span></div>
          <label><span>Repository URL</span><div className="input-icon"><Link2 /><input value={remoteUrl} onChange={(event) => setRemoteUrl(event.target.value)} placeholder="https://github.com/owner/repository" /></div></label>
          <div className="github-connect-actions"><button className="button" disabled={busy} onClick={() => void action('/api/github/disconnect', {}, 'GitHub disconnected.').then(() => { setGithub((value) => value ? { ...value, connected: false, login: '' } : value); setConnectionOpen(false); })}><Unplug />Disconnect</button><button className="button primary" disabled={busy || !remoteUrl.trim()} onClick={() => void action('/api/git/remote/set', { url: remoteUrl, path: root }, 'GitHub remote updated.')}><Link2 />Update remote</button></div>
        </> : <>
          <p>Use a new fine-grained token with repository Contents read/write access. The token is stored locally with restricted file permissions and is never written into the Git remote URL.</p>
          <label><span>Personal access token</span><div className="input-icon"><KeyRound /><input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="github_pat_…" /></div></label>
          <label><span>Repository URL</span><div className="input-icon"><Link2 /><input value={remoteUrl} onChange={(event) => setRemoteUrl(event.target.value)} placeholder="https://github.com/owner/repository" /></div></label>
          <div className="git-identity"><label><span>Commit name</span><input value={username} onChange={(event) => setUsername(event.target.value)} /></label><label><span>Commit email</span><input value={email} onChange={(event) => setEmail(event.target.value)} /></label></div>
          <div className="github-connect-actions"><button className="button primary" disabled={busy || !token.trim()} onClick={() => void connect()}><GitHubIcon />Connect account</button></div>
        </>}
        <div className="models-retired"><Settings2 /><span><strong>GitHub Models unavailable</strong><small>{github?.github_models_message || 'GitHub retired its Models inference service. Use the searchable model picker with free cloud or custom API providers.'}</small></span></div>
      </div>}
    </div>
    {status && !status.is_repo ? <div className="git-empty"><GitBranch /><p>This folder is not a Git repository.</p><button className="button" onClick={() => void action('/api/git/init', { path: root })}>Initialize repository</button></div> : <>
      <div className="branch-line"><GitBranch />{status?.branch || 'Loading…'}<span>{status?.count || 0}</span></div>
      <div className="sync-actions"><button className="button" disabled={!status?.has_remote || busy} onClick={() => void action('/api/git/pull', { path: root }, 'Pull completed.')}><DownloadCloud />Pull</button><button className="button" disabled={!status?.has_remote || busy} onClick={() => void action('/api/git/push', { path: root }, 'Push completed.')}><UploadCloud />Push</button></div>
      <form className="commit-box" onSubmit={(event) => { event.preventDefault(); if (message.trim()) void action('/api/git/commit', { message, stage_all: false, path: root }).then((result) => { if (result) setMessage(''); }); }}><textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Commit message" /><button className="button primary" disabled={!message.trim() || !status?.staged.length || busy}><Check />Commit staged</button></form>
      {Boolean(status?.staged.length) && <><div className="git-group"><span>Staged changes</span><button className="icon-button" onClick={() => void action('/api/git/unstage', { all: true, path: root })} aria-label="Unstage all"><Minus /></button></div>{status?.staged.map(row)}</>}
      {Boolean(status?.unstaged.length) && <><div className="git-group"><span>Changes</span><button className="icon-button" onClick={() => void action('/api/git/stage', { all: true, path: root })} aria-label="Stage all"><Plus /></button></div>{status?.unstaged.map(row)}</>}
      {!status?.count && <div className="git-clean"><Check />Working tree clean</div>}
    </>}
    {notice && <div className={`git-notice ${notice.kind}`}>{notice.text}</div>}
  </section>;
}
