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

const THREADS_PAGE_SIZE = 6;

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
  const [formBusy, setFormBusy] = useState(false);
  const [importingThreadId, setImportingThreadId] = useState<string | null>(null);
  const [showCodexThreads, setShowCodexThreads] = useState(false);
  const [threadPage, setThreadPage] = useState(1);

  useEffect(() => {
    setBackend(defaultBackend);
  }, [defaultBackend]);

  useEffect(() => {
    setCwd(defaultCwd);
  }, [defaultCwd]);

  useEffect(() => {
    setRemoteCwd(defaultRemoteCwd ?? "~");
  }, [defaultRemoteCwd]);

  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(codexThreads.length / THREADS_PAGE_SIZE));
    setThreadPage((current) => Math.min(current, totalPages));
  }, [codexThreads.length]);

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormBusy(true);
    try {
      // Remote launches only use the remote path; let the backend fill cwd
      // from it for the UI label.
      await onCreate(backend, targetLabel ? "" : cwd, title, targetLabel ? remoteCwd : undefined);
      setTitle("");
    } finally {
      setFormBusy(false);
    }
  }

  async function submitAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormBusy(true);
    try {
      await onAttach(tmuxTarget, backend);
      setTmuxTarget("");
    } finally {
      setFormBusy(false);
    }
  }

  async function handleImport(threadId: string) {
    setImportingThreadId(threadId);
    try {
      await onImportCodexThread(threadId);
    } finally {
      setImportingThreadId(null);
    }
  }

  const threadTotalPages = Math.max(
    1,
    Math.ceil(codexThreads.length / THREADS_PAGE_SIZE),
  );
  const threadPageStart = (threadPage - 1) * THREADS_PAGE_SIZE;
  const visibleThreads = codexThreads.slice(
    threadPageStart,
    threadPageStart + THREADS_PAGE_SIZE,
  );
  const showingFrom = threadPageStart + 1;
  const showingTo = Math.min(codexThreads.length, threadPageStart + THREADS_PAGE_SIZE);
  const importSummary = codexThreads.length
    ? `${codexThreads.length} stored threads${targetLabel ? ` on ${targetLabel}` : ""}`
    : `No stored threads${targetLabel ? ` on ${targetLabel}` : ""}`;

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
        <button className="primary" disabled={formBusy} type="submit">
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
        <button className="secondary" disabled={formBusy} type="submit">
          Attach
        </button>
      </form>
      {supportedBackends.includes("codex") ? (
        <section className="panel stack import-thread-panel">
          <div className="field-row import-thread-header">
            <div className="import-thread-summary">
              <h3>Import Codex thread</h3>
              <p className="meta">{importSummary}</p>
            </div>
            <button
              className="secondary"
              disabled={codexThreadsLoading || !codexThreads.length}
              type="button"
              onClick={() => setShowCodexThreads((current) => !current)}
            >
              {showCodexThreads ? "Hide" : "Browse"}
            </button>
          </div>
          {codexThreadsLoading ? <p className="muted">Loading stored threads…</p> : null}
          {!codexThreadsLoading && !codexThreads.length ? (
            <p className="muted">No importable Codex threads found.</p>
          ) : null}
          {!codexThreadsLoading && codexThreads.length && !showCodexThreads ? (
            <p className="meta">
              Hidden by default. Open only when you want to adopt an existing thread.
            </p>
          ) : null}
          {showCodexThreads ? (
            <div className="import-thread-list">
              {visibleThreads.map((thread, index) => {
                const ordinal = String(threadPageStart + index + 1).padStart(2, "0");
                const isImporting = importingThreadId === thread.id;
                return (
                  <article className="import-thread-row" key={thread.id}>
                    <span className="import-thread-index" aria-hidden="true">
                      {ordinal}
                    </span>
                    <div className="import-thread-body">
                      <div className="import-thread-headline">
                        <h4 title={thread.title}>{thread.title}</h4>
                        <time
                          className="import-thread-time"
                          dateTime={thread.updated_at}
                        >
                          {formatRelativeTime(thread.updated_at)}
                        </time>
                      </div>
                      {thread.preview ? (
                        <p className="import-thread-preview" title={thread.preview}>
                          {thread.preview}
                        </p>
                      ) : null}
                      <div className="import-thread-bottomline">
                        <div className="import-thread-tags">
                          {thread.branch ? (
                            <span className="import-thread-chip">
                              {thread.branch}
                            </span>
                          ) : null}
                          {thread.repo_name ? (
                            <span className="import-thread-repo">
                              {thread.repo_name}
                            </span>
                          ) : null}
                          <span
                            className="import-thread-cwd"
                            title={thread.cwd}
                          >
                            {thread.cwd}
                          </span>
                        </div>
                        <div className="import-thread-cta">
                          <button
                            className="secondary"
                            disabled={importingThreadId !== null}
                            type="button"
                            onClick={() => void handleImport(thread.id)}
                          >
                            {isImporting ? "Importing…" : "Import →"}
                          </button>
                        </div>
                      </div>
                    </div>
                  </article>
                );
              })}
              {threadTotalPages > 1 ? (
                <div className="import-thread-footer">
                  <span>
                    {showingFrom}–{showingTo} of {codexThreads.length}
                  </span>
                  <div className="import-thread-pager">
                    <button
                      className="secondary"
                      type="button"
                      onClick={() =>
                        setThreadPage((current) => Math.max(1, current - 1))
                      }
                      disabled={threadPage === 1}
                    >
                      ← Prev
                    </button>
                    <span className="import-thread-page-indicator">
                      {threadPage} / {threadTotalPages}
                    </span>
                    <button
                      className="secondary"
                      type="button"
                      onClick={() =>
                        setThreadPage((current) =>
                          Math.min(threadTotalPages, current + 1),
                        )
                      }
                      disabled={threadPage === threadTotalPages}
                    >
                      Next →
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}

function formatRelativeTime(value: string): string {
  const then = new Date(value).getTime()
  const now = Date.now()
  const deltaSeconds = Math.round((then - now) / 1000)
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ]
  for (const [unit, seconds] of units) {
    if (Math.abs(deltaSeconds) >= seconds || unit === "minute") {
      const amount = Math.round(deltaSeconds / seconds)
      return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(
        amount,
        unit,
      )
    }
  }
  return "just now"
}
