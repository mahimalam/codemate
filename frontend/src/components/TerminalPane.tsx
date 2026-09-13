import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { Maximize2, Minimize2, Trash2, X } from 'lucide-react';

export function TerminalPane({ cwd, onClose, commandRequest }: { cwd: string | null; onClose: () => void; commandRequest?: { id: number; text: string } }) {
  const host = useRef<HTMLDivElement>(null);
  const terminal = useRef<Terminal | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const sentCommand = useRef(0);
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    if (!host.current) return;
    const instance = new Terminal({
      fontFamily: "'JetBrains Mono', 'SFMono-Regular', Consolas, monospace", fontSize: 12.5, lineHeight: 1.45,
      cursorBlink: true, cursorStyle: 'bar', convertEol: true,
      theme: { background: '#0a0d13', foreground: '#c7cfdd', cursor: '#8b9cff', selectionBackground: '#37486d88', black: '#11151d', red: '#f07178', green: '#9ece6a', yellow: '#e0af68', blue: '#7aa2f7', magenta: '#bb9af7', cyan: '#7dcfff', white: '#c0caf5' },
    });
    const fit = new FitAddon(); instance.loadAddon(fit); instance.open(host.current); fit.fit(); terminal.current = instance;
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${location.host}/ws/terminal?cwd=${encodeURIComponent(cwd || '')}`);
    socketRef.current = socket;
    socket.onopen = () => { instance.writeln('\x1b[38;2;139;156;255mCodeMate terminal connected\x1b[0m'); fit.fit(); };
    socket.onmessage = async (event) => instance.write(typeof event.data === 'string' ? event.data : await event.data.text());
    socket.onclose = () => instance.writeln('\r\n\x1b[38;2;240;113;120mTerminal disconnected\x1b[0m');
    const dataListener = instance.onData((data) => { if (socket.readyState === WebSocket.OPEN) socket.send(new TextEncoder().encode(data)); });
    const resizeObserver = new ResizeObserver(() => { fit.fit(); if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'resize', cols: instance.cols, rows: instance.rows })); });
    resizeObserver.observe(host.current);
    return () => { resizeObserver.disconnect(); dataListener.dispose(); socket.close(); instance.dispose(); terminal.current = null; socketRef.current = null; };
  }, [cwd]);

  useEffect(() => {
    if (!commandRequest || sentCommand.current === commandRequest.id) return;
    const send = () => {
      if (socketRef.current?.readyState !== WebSocket.OPEN) return;
      socketRef.current.send(new TextEncoder().encode(`${commandRequest.text}\r`));
      sentCommand.current = commandRequest.id;
    };
    send();
    const timer = window.setInterval(() => { send(); if (sentCommand.current === commandRequest.id) window.clearInterval(timer); }, 100);
    return () => window.clearInterval(timer);
  }, [commandRequest]);

  return <section className={`terminal-panel ${maximized ? 'maximized' : ''}`}><div className="panel-tabs"><span className="panel-tab active">Terminal</span><span className="panel-spacer" /><button className="icon-button" onClick={() => terminal.current?.clear()} title="Clear terminal" aria-label="Clear terminal"><Trash2 /></button><button className="icon-button" onClick={() => setMaximized((value) => !value)} title={maximized ? 'Restore panel' : 'Maximize panel'} aria-label={maximized ? 'Restore terminal panel' : 'Maximize terminal panel'}>{maximized ? <Minimize2 /> : <Maximize2 />}</button><button className="icon-button" onClick={onClose} title="Close terminal" aria-label="Close terminal"><X /></button></div><div ref={host} className="terminal-host" /></section>;
}
