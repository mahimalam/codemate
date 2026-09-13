export type Workspace = {
  has_workspace: boolean;
  current_path: string | null;
  name: string;
  short_path: string;
  recent: Array<{ name: string; path: string }>;
};

export type TreeEntry = { name: string; path: string; is_dir: boolean; size: number };
export type GitFile = { status: string; file: string; staged: boolean };
export type GitStatus = {
  branch: string;
  is_repo: boolean;
  files: GitFile[];
  staged: GitFile[];
  unstaged: GitFile[];
  count: number;
  remote_url?: string;
  has_remote?: boolean;
};
export type ModelInfo = { provider: string; model: string; name: string; tier?: string; badge?: string; description?: string };
export type ModelsResponse = {
  active_provider: string;
  active_model: string;
  active_tier?: 'fast' | 'complex';
  fast_models?: ModelInfo[];
  complex_models?: ModelInfo[];
  local_models?: Array<{ name: string }>;
};
export type HistorySummary = { id: string; title?: string; updated_at?: string; workspace?: string };
export type ChatMessage = { id?: string; role: 'user' | 'assistant'; content: string; created_at?: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch { /* use status */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown = {}) => request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

export async function streamChat(
  payload: Record<string, unknown>,
  onEvent: (event: Record<string, unknown>) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok || !response.body) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch { /* use status */ }
    throw new Error(detail);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';
    for (const frame of frames) {
      const line = frame.split('\n').find((item) => item.startsWith('data: '));
      if (!line || line === 'data: [DONE]') continue;
      onEvent(JSON.parse(line.slice(6)) as Record<string, unknown>);
    }
  }
}

export function languageForPath(path: string): string {
  const extension = path.split('.').pop()?.toLowerCase();
  return ({
    ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript', py: 'python',
    json: 'json', md: 'markdown', html: 'html', css: 'css', scss: 'scss', yaml: 'yaml',
    yml: 'yaml', sh: 'shell', sql: 'sql', rs: 'rust', go: 'go', java: 'java', cpp: 'cpp',
  } as Record<string, string>)[extension || ''] || 'plaintext';
}
