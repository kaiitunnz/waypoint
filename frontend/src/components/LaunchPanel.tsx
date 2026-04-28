"use client";

import { FormEvent, useEffect, useState } from "react";

import { Backend } from "@/lib/types";

interface LaunchPanelProps {
  defaultBackend: Backend;
  defaultCwd: string;
  defaultRemoteCwd: string;
  remoteCodexEnabled: boolean;
  onCreate: (backend: Backend, cwd: string, title: string, remoteCwd?: string) => Promise<void>;
  onAttach: (target: string, backendHint: Backend) => Promise<void>;
}

export function LaunchPanel({
  defaultBackend,
  defaultCwd,
  defaultRemoteCwd,
  remoteCodexEnabled,
  onCreate,
  onAttach,
}: LaunchPanelProps) {
  const [backend, setBackend] = useState<Backend>(defaultBackend);
  const [cwd, setCwd] = useState(defaultCwd);
  const [remoteCwd, setRemoteCwd] = useState(defaultRemoteCwd);
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
    setRemoteCwd(defaultRemoteCwd);
  }, [defaultRemoteCwd]);

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      await onCreate(backend, cwd, title, backend === "codex" && remoteCodexEnabled ? remoteCwd : undefined);
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
            <option value="codex">Codex</option>
            <option value="claude_code">Claude Code</option>
          </select>
        </label>
        <label className="field">
          <span>Working directory</span>
          <input value={cwd} onChange={(event) => setCwd(event.target.value)} />
        </label>
        {backend === "codex" && remoteCodexEnabled ? (
          <label className="field">
            <span>Remote working directory</span>
            <input value={remoteCwd} onChange={(event) => setRemoteCwd(event.target.value)} />
          </label>
        ) : null}
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
    </section>
  );
}
