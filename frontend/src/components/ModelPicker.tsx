import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, Search } from 'lucide-react';
import type { ModelInfo } from '../api';

export function ModelPicker({ models, provider, model, onSelect }: { models: ModelInfo[]; provider: string; model: string; onSelect: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const selected = models.find((entry) => entry.provider === provider && entry.model === model);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? models.filter((entry) => `${entry.name} ${entry.model} ${entry.provider} ${entry.badge || ''} ${entry.description || ''}`.toLowerCase().includes(needle)) : models;
  }, [models, query]);
  const groups = useMemo(() => {
    const grouped = new Map<string, ModelInfo[]>();
    for (const entry of filtered) grouped.set(entry.provider, [...(grouped.get(entry.provider) || []), entry]);
    return grouped;
  }, [filtered]);

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const close = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    window.addEventListener('pointerdown', close);
    return () => window.removeEventListener('pointerdown', close);
  }, [open]);

  return <div className="model-picker" ref={root}>
    <button type="button" className="model-picker-trigger" onClick={() => { setOpen((value) => !value); setQuery(''); }} aria-haspopup="listbox" aria-expanded={open} title={selected?.name || model}>
      <span><strong>{selected?.name || model || 'Choose model'}</strong><small>{selected?.badge || provider}</small></span><ChevronDown />
    </button>
    {open && <div className="model-picker-popover">
      <div className="model-picker-search"><Search /><input ref={input} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Escape') setOpen(false); }} placeholder="Search models, providers, or capabilities" aria-label="Search models" /><span>{filtered.length}</span></div>
      <div className="model-picker-list" role="listbox" aria-label="Available models">
        {[...groups.entries()].map(([group, entries]) => <section className="model-provider-group" key={group}><div>{group.replaceAll('_', ' ')}</div>{entries.map((entry) => {
          const active = entry.provider === provider && entry.model === model;
          return <button type="button" role="option" aria-selected={active} className={active ? 'active' : ''} key={`${entry.provider}:${entry.model}`} onClick={() => { onSelect(`${entry.provider}::${entry.model}`); setOpen(false); }}><span className="model-check">{active && <Check />}</span><span><strong>{entry.name}</strong><small>{entry.description || entry.model}</small></span><b>{entry.badge || entry.tier}</b></button>;
        })}</section>)}
        {!filtered.length && <div className="model-picker-empty">No model matches “{query}”.</div>}
      </div>
    </div>}
  </div>;
}
