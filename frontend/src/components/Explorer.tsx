import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { ChevronsDownUp, ChevronDown, ChevronRight, File, FilePenLine, FilePlus, Folder, FolderOpen, FolderPlus, RefreshCw, Search, Trash2 } from 'lucide-react';
import { api, TreeEntry } from '../api';

type Props = {
  mode: 'explorer' | 'search';
  root: string | null;
  activePath: string | null;
  version: number;
  onOpen: (path: string) => void;
  onChanged: () => void;
  onRenamed: (oldPath: string, newPath: string) => void;
  onDeleted: (path: string) => void;
};

type Selection = { path: string; isDir: boolean } | null;

function TreeNode({ item, activePath, selection, version, collapseVersion, onSelect, onOpen, onRenamed, onDeleted }: {
  item: TreeEntry;
  activePath: string | null;
  selection: Selection;
  version: number;
  collapseVersion: number;
  onSelect: (item: TreeEntry) => void;
  onOpen: (path: string) => void;
  onRenamed: (oldPath: string, newPath: string) => void;
  onDeleted: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState<TreeEntry[]>([]);
  const [loading, setLoading] = useState(false);

  async function loadChildren() {
    if (!item.is_dir) return;
    setLoading(true);
    try { setChildren(await api.get(`/api/fs/tree?path=${encodeURIComponent(item.path)}`)); }
    finally { setLoading(false); }
  }

  useEffect(() => { if (open) void loadChildren(); }, [version]);
  useEffect(() => { setOpen(false); }, [collapseVersion]);

  async function activate() {
    onSelect(item);
    if (!item.is_dir) { onOpen(item.path); return; }
    if (!open) await loadChildren();
    setOpen((value) => !value);
  }

  async function rename(event: MouseEvent) {
    event.stopPropagation();
    const name = window.prompt(`Rename ${item.name}`, item.name)?.trim();
    if (!name || name === item.name || name.includes('/') || name.includes('\\')) return;
    const parent = item.path.slice(0, Math.max(item.path.lastIndexOf('/'), item.path.lastIndexOf('\\')));
    const newPath = `${parent}${parent.includes('\\') ? '\\' : '/'}${name}`;
    await api.post('/api/fs/rename', { old_path: item.path, new_path: newPath });
    onRenamed(item.path, newPath);
  }

  async function remove(event: MouseEvent) {
    event.stopPropagation();
    if (!window.confirm(`Delete ${item.name}? This cannot be undone.`)) return;
    await api.post('/api/fs/delete', { path: item.path });
    onDeleted(item.path);
  }

  const selected = selection?.path === item.path || activePath === item.path;
  return <div className="tree-node" role="treeitem" aria-expanded={item.is_dir ? open : undefined}>
    <div className={`tree-row ${selected ? 'selected' : ''}`} onClick={() => void activate()} title={item.path}>
      <span className="tree-chevron">{item.is_dir ? (open ? <ChevronDown /> : <ChevronRight />) : null}</span>
      {item.is_dir ? (open ? <FolderOpen className="folder-icon" /> : <Folder className="folder-icon" />) : <File />}
      <span className="tree-name">{item.name}</span>
      <span className="tree-actions">
        <button className="icon-button tree-action" aria-label={`Rename ${item.name}`} onClick={(event) => void rename(event)}><FilePenLine /></button>
        <button className="icon-button tree-action" aria-label={`Delete ${item.name}`} onClick={(event) => void remove(event)}><Trash2 /></button>
      </span>
    </div>
    {open && <div className="tree-children" role="group">
      {loading ? <div className="tree-loading">Loading…</div> : children.map((child) => <TreeNode key={child.path} item={child} activePath={activePath} selection={selection} version={version} collapseVersion={collapseVersion} onSelect={onSelect} onOpen={onOpen} onRenamed={onRenamed} onDeleted={onDeleted} />)}
      {!loading && !children.length && <div className="tree-loading">Empty folder</div>}
    </div>}
  </div>;
}

export function Explorer({ mode, root, activePath, version, onOpen, onChanged, onRenamed, onDeleted }: Props) {
  const [items, setItems] = useState<TreeEntry[]>([]);
  const [selection, setSelection] = useState<Selection>(null);
  const [rootOpen, setRootOpen] = useState(true);
  const [collapseVersion, setCollapseVersion] = useState(0);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<Array<{ file: string; full_path: string; line: string; preview: string }>>([]);
  const searchInput = useRef<HTMLInputElement>(null);

  async function refresh() {
    setItems(root ? await api.get(`/api/fs/tree?path=${encodeURIComponent(root)}`) : []);
  }
  useEffect(() => { void refresh(); }, [root, version]);
  useEffect(() => { if (mode === 'search') searchInput.current?.focus(); }, [mode]);

  async function create(kind: 'file' | 'folder') {
    if (!root) return;
    const name = window.prompt(`New ${kind} name`)?.trim();
    if (!name || name.includes('/') || name.includes('\\')) return;
    const selectedDirectory = selection?.isDir ? selection.path : selection?.path.slice(0, Math.max(selection.path.lastIndexOf('/'), selection.path.lastIndexOf('\\')));
    const parent = selectedDirectory || root;
    const path = `${parent}${parent.includes('\\') ? '\\' : '/'}${name}`;
    if (kind === 'folder') await api.post('/api/fs/mkdir', { path });
    else await api.post('/api/fs/write', { path, content: '' });
    onChanged();
    if (kind === 'file') onOpen(path);
  }

  useEffect(() => {
    if (mode !== 'search' || !query.trim() || !root) { setResults([]); return; }
    const handle = window.setTimeout(async () => {
      setSearching(true);
      try { setResults(await api.get(`/api/fs/search?query=${encodeURIComponent(query)}&path=${encodeURIComponent(root)}`)); }
      finally { setSearching(false); }
    }, 250);
    return () => window.clearTimeout(handle);
  }, [mode, query, root]);

  if (mode === 'search') return <section className="sidebar-section explorer" aria-label="Search">
    <div className="section-heading"><strong>Search</strong></div>
    <label className="search-field"><Search /><input ref={searchInput} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search text in files" aria-label="Search text in files" /></label>
    <div className="search-results">
      {!root && <div className="empty-small">Open a folder to search files.</div>}
      {root && !query.trim() && <div className="empty-small">Type a word or phrase to search the workspace.</div>}
      {searching ? <div className="empty-small">Searching…</div> : results.map((result) => <button className="search-result" key={`${result.full_path}:${result.line}`} onClick={() => onOpen(result.full_path)}><span>{result.file}:{result.line}</span><small>{result.preview}</small></button>)}
      {!searching && query.trim() && !results.length && <div className="empty-small">No matches found.</div>}
    </div>
  </section>;

  const rootName = root?.split(/[\\/]/).filter(Boolean).pop() || 'Workspace';
  return <section className="sidebar-section explorer" aria-label="Explorer">
    <div className="section-heading">
      <strong>Explorer</strong>
      <div className="heading-actions">
        <button className="icon-button" onClick={() => void create('file')} aria-label="New file" title="New file in selected folder"><FilePlus /></button>
        <button className="icon-button" onClick={() => void create('folder')} aria-label="New folder" title="New folder in selected folder"><FolderPlus /></button>
        <button className="icon-button" onClick={() => void refresh()} aria-label="Refresh explorer" title="Refresh"><RefreshCw /></button>
        <button className="icon-button" onClick={() => { setCollapseVersion((value) => value + 1); setRootOpen(false); }} aria-label="Collapse folders" title="Collapse folders"><ChevronsDownUp /></button>
      </div>
    </div>
    {root && <button className="workspace-root" onClick={() => setRootOpen((value) => !value)} aria-expanded={rootOpen}><span>{rootOpen ? <ChevronDown /> : <ChevronRight />}</span><FolderOpen />{rootName}</button>}
    <div className="tree" role="tree">
      {rootOpen && items.map((item) => <TreeNode key={item.path} item={item} activePath={activePath} selection={selection} version={version} collapseVersion={collapseVersion} onSelect={(entry) => setSelection({ path: entry.path, isDir: entry.is_dir })} onOpen={onOpen} onRenamed={onRenamed} onDeleted={onDeleted} />)}
      {!root && <div className="empty-small">Open a folder to browse files.</div>}
    </div>
  </section>;
}
