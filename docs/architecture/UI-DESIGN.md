# IDE architecture and design system

VexP Code is an Electron desktop application with a React and TypeScript renderer. The renderer is deliberately small at the document boundary: `frontend/index.html` mounts the application, while the workbench lives in typed components under `frontend/src`.

## Workbench layout

```text
Title bar: workspace · search · terminal · agent · settings
Activity rail: Explorer · Search · Source control · History
Main area: file tree | Monaco editor and terminal | agent chat
Status bar: workspace · encoding · active language · harness status
```

The layout keeps the editor primary while the agent remains available beside it. Explorer, search, Git, history, terminal, and chat are independently scrollable and resizable. The chat follows streaming output only while the user remains near the bottom; manual scrolling pauses follow mode and exposes a jump-to-latest control.

## Component ownership

| Area | Source |
|---|---|
| Workbench composition and shared workspace state | `frontend/src/App.tsx` |
| API client and SSE chat stream | `frontend/src/api.ts` |
| Agent composer, run details, model selection, access controls | `frontend/src/components/ChatPanel.tsx` |
| Monaco editor | `frontend/src/components/EditorPane.tsx` |
| Workspace explorer and search | `frontend/src/components/Explorer.tsx` |
| Git and GitHub controls | `frontend/src/components/GitPanel.tsx` |
| Provider and custom API settings | `frontend/src/components/SettingsModal.tsx` |
| Terminal bridge | `frontend/src/components/TerminalPane.tsx` |
| Semantic tokens, layout, type, motion, and responsive rules | `frontend/src/styles.css` |

## Visual language

The application uses dark neutral surfaces, restrained blue interaction states, green success, amber pending state, and red errors. Inter is used for interface and chat text; JetBrains Mono is used for code, paths, commands, and terminal output. Lucide SVG icons replace decorative emoji and inconsistent logos.

Chat messages use safe Markdown rendering with readable line height, normal prose fonts, code blocks, tables, and links. The run card is collapsible so tool activity remains available without dominating the conversation. It retains the existing subtle activity animation while a run is active and settles after completion.

## Interaction requirements

- All icon-only controls have accessible labels.
- Right-click and touchpad context menus support Copy, Paste, Cut, Undo, Redo, and Select All where relevant.
- Provider settings expose free cloud, local, paid, and custom OpenAI- or Anthropic-compatible models. Saved custom models appear in the chat model picker.
- Errors remain visible even when a model emitted partial text.
- The selected model route is visible after automatic routing resolves it.
