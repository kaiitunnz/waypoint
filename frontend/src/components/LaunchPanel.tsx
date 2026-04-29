"use client";

import { FormEvent, useEffect, useState } from "react";

import { Backend, CodexThreadSummary } from "@/lib/types";

interface LaunchPanelProps {
  defaultBackend: Backend;
  defaultCwd: string;
  defaultRemoteCwd: string | null;
  targetLabel: string | null;
  supportedBackends: Backend[];
  codexThreads: CodexThreadSummary[];
  codexThreadsLoading: boolean;
  onCreate: (backend: Backend, cwd: string, title: string, remoteCwd?: string) => Promise<void>;
  onAttach: (target: string, backendHint: Backend) => Promise<void>;
  onImportCodexThread: (threadId: string) => Promise<void>;
}

export function LaunchPanel({
  defaultBackend,
  defaultCwd,
  defaultRemoteCwd,
  targetLabel,
  supportedBackends,
  codexThreads,
  codexThreadsLoading,
  onCreate,
  onAttach,
  onImportCodexThread,
}: LaunchPanelProps) {
  const [backend, setBackend] = useState<Backend>(defaultBackend);
  const [cwd, setCwd] = useState(defaultCwd);
  const [remoteCwd, setRemoteCwd] = useState(defaultRemoteCwd ?? "~");
  const [title, setTitle] = useState("");
  const [tmuxTarget, setTmuxTarget] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setBackend(defaultBackend);
  }, [defaultBackend]);

  useEffect(() => {
    setCwd(defaultCwd);
  }, [defaultCwd]);

  useEffect(() => {
    setRemoteCwd(defaultRemoteCwd ?? "~");
  }, [defaultRemoteCwd]);

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      // Remote launches only use the remote path; let the backend fill cwd
      // from it for the UI label.
      await onCreate(backend, targetLabel ? "" : cwd, title, targetLabel ? remoteCwd : undefined);
      setTitle("");
    } finally {
      setBusy(false);
    }
  }

  async function submitAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      await onAttach(tmuxTarget, backend);
      setTmuxTarget("");
    } finally {
      setBusy(false);
    }
  }

  async function handleImport(threadId: string) {
    setBusy(true);
    try {
      await onImportCodexThread(threadId);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="launch-grid">
      <form className="panel stack" onSubmit={submitCreate}>
        <div>
          <h3>New session</h3>
          <p className="muted">Launch through the wrapper for better transcript fidelity.</p>
        </div>
        <label className="field">
          <span>Backend</span>
          <select value={backend} onChange={(event) => setBackend(event.target.value as Backend)}>
            {supportedBackends.includes("codex") ? <option value="codex">Codex</option> : null}
            {supportedBackends.includes("claude_code") ? <option value="claude_code">Claude Code</option> : null}
          </select>
        </label>
        {targetLabel ? (
          <label className="field">
            <span>Working directory on {targetLabel}</span>
            <input
              value={remoteCwd}
              onChange={(event) => setRemoteCwd(event.target.value)}
              placeholder="~"
            />
          </label>
        ) : (
          <label className="field">
            <span>Working directory</span>
            <input value={cwd} onChange={(event) => setCwd(event.target.value)} />
          </label>
        )}
        <label className="field">
          <span>Title</span>
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Optional" />
        </label>
        <button className="primary" disabled={busy} type="submit">
          Launch
        </button>
      </form>
      <form className="panel stack" onSubmit={submitAttach}>
        <div>
          <h3>Attach tmux</h3>
          <p className="muted">Observe an existing pane with raw terminal fallback.</p>
        </div>
        <label className="field">
          <span>Tmux target</span>
          <input value={tmuxTarget} onChange={(event) => setTmuxTarget(event.target.value)} placeholder="session:0.0" />
        </label>
        <label className="field">
          <span>Backend hint</span>
          <select value={backend} onChange={(event) => setBackend(event.target.value as Backend)}>
            <option value="codex">Codex</option>
            <option value="claude_code">Claude Code</option>
          </select>
        </label>
        <button className="secondary" disabled={busy} type="submit">
          Attach
        </button>
      </form>
      {supportedBackends.includes("codex") ? (
        <section className="panel stack">
          <div>
            <h3>Import Codex thread</h3>
            <p className="muted">
              Resume a stored Codex thread{targetLabel ? ` on ${targetLabel}` : ""}.
            </p>
          </div>
          {codexThreadsLoading ? <p className="muted">Loading stored threads…</p> : null}
          {!codexThreadsLoading && !codexThreads.length ? (
            <p className="muted">No importable Codex threads found.</p>
          ) : null}
          {codexThreads.map((thread) => (
            <article className="import-thread-card" key={thread.id}>
              <div className="session-row">
                <span className="badge neutral">Codex</span>
                {thread.branch ? <span className="badge neutral">{thread.branch}</span> : null}
              </div>
              <h3>{thread.title}</h3>
              <p className="muted">{thread.cwd}</p>
              {thread.preview ? <p className="meta">{thread.preview}</p> : null}
              <div className="action-row import-thread-actions">
                <span className="meta">
                  {thread.repo_name ?? "No repo"} · updated{" "}
                  {new Date(thread.updated_at).toLocaleString()}
                </span>
                <button
                  className="secondary"
                  disabled={busy}
                  type="button"
                  onClick={() => void handleImport(thread.id)}
                >
                  Import
                </button>
              </div>
            </article>
          ))}
        </section>
      ) : null}
    </section>
  );
}
