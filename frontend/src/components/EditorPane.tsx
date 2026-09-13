import Editor, { BeforeMount, loader, OnMount } from '@monaco-editor/react';
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';
import 'monaco-editor/esm/vs/language/typescript/monaco.contribution';
import 'monaco-editor/esm/vs/language/json/monaco.contribution';
import 'monaco-editor/esm/vs/language/css/monaco.contribution';
import 'monaco-editor/esm/vs/language/html/monaco.contribution';
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution';
import 'monaco-editor/esm/vs/basic-languages/shell/shell.contribution';
import 'monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution';
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution';
import 'monaco-editor/esm/vs/basic-languages/sql/sql.contribution';
import 'monaco-editor/esm/vs/basic-languages/rust/rust.contribution';
import 'monaco-editor/esm/vs/basic-languages/go/go.contribution';
import 'monaco-editor/esm/vs/basic-languages/java/java.contribution';
import 'monaco-editor/esm/vs/basic-languages/cpp/cpp.contribution';
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import JsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker';
import CssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker';
import HtmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker';
import TsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';
import { Play, Save, X } from 'lucide-react';
import { languageForPath } from '../api';
import { BrandIcon } from './BrandIcon';

(self as unknown as { MonacoEnvironment: { getWorker: (_moduleId: string, label: string) => Worker } }).MonacoEnvironment = {
  getWorker: (_moduleId, label) => {
    if (label === 'json') return new JsonWorker();
    if (label === 'css' || label === 'scss' || label === 'less') return new CssWorker();
    if (label === 'html' || label === 'handlebars' || label === 'razor') return new HtmlWorker();
    if (label === 'typescript' || label === 'javascript') return new TsWorker();
    return new EditorWorker();
  },
};
loader.config({ monaco: monaco as typeof import('monaco-editor') });

export type EditorTab = { path: string; name: string; content: string; saved: string; sha256: string };
type Props = {
  tabs: EditorTab[];
  activePath: string | null;
  onActivate: (path: string) => void;
  onChange: (path: string, content: string) => void;
  onClose: (path: string) => void;
  onSave: (path: string) => void;
  onRun: (path: string) => void;
};

const configure: BeforeMount = (monaco) => {
  monaco.editor.defineTheme('codemate-dark', {
    base: 'vs-dark', inherit: true,
    rules: [
      { token: 'comment', foreground: '667085', fontStyle: 'italic' },
      { token: 'keyword', foreground: 'a78bfa' },
      { token: 'string', foreground: 'a7d8a0' },
      { token: 'number', foreground: 'e8b86d' },
    ],
    colors: {
      'editor.background': '#0d1017', 'editor.foreground': '#d8dee9', 'editorLineNumber.foreground': '#4f596b',
      'editorLineNumber.activeForeground': '#a9b4c7', 'editor.selectionBackground': '#33446a88',
      'editor.lineHighlightBackground': '#141925', 'editorCursor.foreground': '#8b9cff',
      'editorIndentGuide.background1': '#212736', 'editorIndentGuide.activeBackground1': '#39445c',
    },
  });
};

export function EditorPane({ tabs, activePath, onActivate, onChange, onClose, onSave, onRun }: Props) {
  const active = tabs.find((tab) => tab.path === activePath) || null;
  const mount: OnMount = (editor) => { editor.focus(); };
  return <main className="editor-pane">
    {tabs.length > 0 && <div className="editor-tabs" role="tablist">
      {tabs.map((tab) => <button key={tab.path} role="tab" aria-selected={tab.path === activePath} className={`editor-tab ${tab.path === activePath ? 'active' : ''}`} onClick={() => onActivate(tab.path)}>
        <span>{tab.name}</span>{tab.content !== tab.saved && <span className="dirty-dot" aria-label="Unsaved" />}
        <span className="tab-close" role="button" aria-label={`Close ${tab.name}`} onClick={(event) => { event.stopPropagation(); onClose(tab.path); }}><X /></span>
      </button>)}
    </div>}
    {active ? <>
      <div className="editor-toolbar"><span className="breadcrumb">{active.path}</span><div><button className="tool-button" onClick={() => onRun(active.path)}><Play />Run</button><button className="tool-button primary" disabled={active.content === active.saved} onClick={() => onSave(active.path)}><Save />Save</button></div></div>
      <div className="monaco-wrap"><Editor path={active.path} value={active.content} language={languageForPath(active.path)} theme="codemate-dark" beforeMount={configure} onMount={mount} onChange={(value) => onChange(active.path, value || '')} options={{ fontFamily: "'JetBrains Mono', 'SFMono-Regular', Consolas, monospace", fontSize: 13, lineHeight: 21, minimap: { enabled: true }, padding: { top: 16 }, smoothScrolling: true, cursorSmoothCaretAnimation: 'on', bracketPairColorization: { enabled: true }, guides: { bracketPairs: true }, renderWhitespace: 'selection', automaticLayout: true }} /> </div>
    </> : <div className="editor-empty"><div className="mark"><BrandIcon /></div><h1>CodeMate</h1><p>Open a file from Explorer or ask the agent to inspect your workspace.</p><div className="shortcuts"><kbd>Ctrl</kbd><kbd>P</kbd><span>Quick open</span><kbd>Ctrl</kbd><kbd>S</kbd><span>Save file</span></div></div>}
  </main>;
}
