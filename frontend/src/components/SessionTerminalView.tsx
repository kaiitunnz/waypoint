"use client";

import { Dispatch, MutableRefObject, SetStateAction, useCallback, useState } from "react";

import { SessionUsagePill } from "@/components/SessionUsagePill";
import { TerminalCompose } from "@/components/TerminalCompose";
import { TerminalScrollChips } from "@/components/TerminalScrollChips";
import { XTerminal, type XTerminalHandle } from "@/components/XTerminal";
import type { TerminalSubmitResult } from "@/lib/composer";
import { SessionRecord } from "@/lib/types";

type Connection = "idle" | "connecting" | "open" | "reconnecting";

interface SessionTerminalViewProps {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  liveTmux: boolean;
  terminalRef: MutableRefObject<XTerminalHandle | null>;
  theme: "dark" | "light";
  terminalDims: { cols: number; rows: number } | null;
  snapshotLoading: boolean;
  sessionExited: boolean;
  dormantReattach: boolean;
  termMenuOpen: boolean;
  setTermMenuOpen: Dispatch<SetStateAction<boolean>>;
  termMenuWrapRef: MutableRefObject<HTMLDivElement | null>;
  termAtBottom: boolean;
  connection: Connection;
  rateLimitRefreshBusy: boolean;
  onRateLimitRefresh: () => void | Promise<void>;
  // True on terminal-only (tmux) sessions where no composer follows — the
  // pane sticky-locks to the viewport bottom once the user scrolls past
  // the SessionHeader. Chat-capable Terminal tabs stay bounded so the
  // composer beneath remains visible.
  locked: boolean;
  onTerminalInput: (data: string) => void;
  // Resolves a ``TerminalSubmitResult`` so the composer can keep the draft
  // and surface a retry hint when the WS is closed, stay silent when a
  // control command reported its own error, or clear it on success. Async
  // because Waypoint control commands (e.g. ``/new``) are handled here.
  onTerminalSubmit: (text: string) => Promise<TerminalSubmitResult>;
  // Attachment-bearing submits route through the HTTP input endpoint (which
  // appends the host file path for tmux) rather than the terminal WS.
  onTerminalSubmitWithAttachments: (
    text: string,
    attachmentIds: string[],
  ) => Promise<TerminalSubmitResult>;
  attachmentsEnabled: boolean;
  onOpenFiles: () => void;
  onRequestPaste: () => void;
  onTerminalResize: (size: { cols: number; rows: number }) => void;
  onTerminalScrollChip: (direction: "up" | "down") => void;
  onTerminalScrollChange: (atBottom: boolean) => void;
  onJumpToLive: () => void;
  // Refresh means "re-seed the terminal from the server". For live tmux
  // that's a WS reconnect; for read-only snapshots that's a REST fetch.
  // The parent picks which based on session.transport.
  onRefresh: () => void;
  onReattach: () => void | Promise<void>;
  onTerminate: () => void | Promise<void>;
  onRemoveFromList: () => void | Promise<void>;
  onSwitchSession: () => void;
  onError: (message: string) => void;
}

export function SessionTerminalView({
  host,
  token,
  sessionId,
  session,
  liveTmux,
  terminalRef,
  theme,
  terminalDims,
  snapshotLoading,
  sessionExited,
  dormantReattach,
  termMenuOpen,
  setTermMenuOpen,
  termMenuWrapRef,
  termAtBottom,
  connection,
  rateLimitRefreshBusy,
  onRateLimitRefresh,
  locked,
  onTerminalInput,
  onTerminalSubmit,
  onTerminalSubmitWithAttachments,
  attachmentsEnabled,
  onOpenFiles,
  onRequestPaste,
  onTerminalResize,
  onTerminalScrollChip,
  onTerminalScrollChange,
  onJumpToLive,
  onRefresh,
  onReattach,
  onTerminate,
  onRemoveFromList,
  onSwitchSession,
  onError,
}: SessionTerminalViewProps) {
  // Primary action shown as a pill button next to the overflow trigger.
  // Only states the user can't trivially reach inside the pane — Reconnect
  // when exited (the pane is gone) and Refresh for read-only snapshots.
  // We deliberately do NOT surface "Interrupt" here because the keybar's
  // ^C already sends SIGINT to the pane (the backend /interrupt action
  // for tmux just writes ^C anyway).
  type PrimaryAction = {
    kind: "refresh" | "reconnect";
    label: string;
    onClick: () => void;
    tone?: "primary";
  };
  let primary: PrimaryAction | null = null;
  if (dormantReattach) {
    primary = {
      kind: "reconnect",
      label: "Reconnect",
      onClick: () => void onReattach(),
      tone: "primary",
    };
  } else if (!liveTmux && session) {
    primary = {
      kind: "refresh",
      label: snapshotLoading ? "Refreshing…" : "Refresh",
      onClick: onRefresh,
    };
  }

  const closeMenu = () => setTermMenuOpen(false);
  const fireFromMenu = (cb: () => void | Promise<void>) => {
    closeMenu();
    void cb();
  };

  const [composeOpen, setComposeOpen] = useState(false);
  // The compose drawer collapses back to a hairline handle when the
  // session isn't live, so we don't carry stale "open" state across a
  // disconnect / view switch.
  const composeEnabled = liveTmux;
  const refocusTerminal = useCallback(() => {
    terminalRef.current?.focus();
  }, [terminalRef]);

  return (
    <section
      className={`session-terminal ${locked ? "is-locked" : ""} ${
        composeEnabled && composeOpen ? "has-compose-open" : ""
      }`}
      aria-label="Terminal session"
    >
      <div className="term-bar">
        <SessionUsagePill
          session={session}
          connection={connection}
          onRateLimitRefresh={onRateLimitRefresh}
          rateLimitRefreshBusy={rateLimitRefreshBusy}
        />
        <span className="term-bar-spacer" />
        {liveTmux && terminalDims ? (
          <span className="term-bar-dims" aria-label="Pane dimensions">
            {terminalDims.cols}×{terminalDims.rows}
          </span>
        ) : null}
        {primary ? (
          <button
            type="button"
            className={`term-bar-action ${primary.tone === "primary" ? "primary" : ""}`}
            onClick={primary.onClick}
            disabled={snapshotLoading && primary.kind === "refresh"}
          >
            {primary.label}
          </button>
        ) : null}
        <div className="term-bar-overflow" ref={termMenuWrapRef}>
          <button
            type="button"
            className={`composer-overflow-trigger ${termMenuOpen ? "open" : ""}`}
            aria-label="More actions"
            aria-haspopup="menu"
            aria-expanded={termMenuOpen}
            onClick={() => setTermMenuOpen((v) => !v)}
          >
            ⋯
          </button>
          {termMenuOpen ? (
            <div className="composer-overflow-menu term-bar-overflow-menu" role="menu">
              {session && primary?.kind !== "refresh" ? (
                <button
                  type="button"
                  role="menuitem"
                  className="composer-overflow-item"
                  onClick={() => {
                    closeMenu();
                    onRefresh();
                  }}
                  disabled={snapshotLoading}
                >
                  <span className="glyph">↻</span>
                  Refresh
                </button>
              ) : null}
              {dormantReattach && primary?.kind !== "reconnect" ? (
                <button
                  type="button"
                  role="menuitem"
                  className="composer-overflow-item"
                  onClick={() => fireFromMenu(onReattach)}
                >
                  <span className="glyph">↺</span>
                  Reconnect session
                </button>
              ) : null}
              <button
                type="button"
                role="menuitem"
                className="composer-overflow-item"
                onClick={() => {
                  closeMenu();
                  onSwitchSession();
                }}
              >
                <span className="glyph">⇄</span>
                Switch session…
              </button>
              {session && !sessionExited ? (
                <>
                  <div className="composer-overflow-separator" />
                  <button
                    type="button"
                    role="menuitem"
                    className="composer-overflow-item danger"
                    onClick={() => fireFromMenu(onTerminate)}
                  >
                    <span className="glyph">⏻</span>
                    Terminate session
                  </button>
                </>
              ) : null}
              {sessionExited ? (
                <>
                  <div className="composer-overflow-separator" />
                  <button
                    type="button"
                    role="menuitem"
                    className="composer-overflow-item danger"
                    onClick={() => fireFromMenu(onRemoveFromList)}
                  >
                    <span className="glyph">✕</span>
                    Delete transcript
                  </button>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
      <div className="term-stage">
        <div className="term-stage-host" role="log" aria-live="polite">
          <XTerminal
            ref={terminalRef}
            theme={theme}
            readOnly={!liveTmux}
            onData={liveTmux ? onTerminalInput : undefined}
            onResize={onTerminalResize}
            onScrollChange={liveTmux ? onTerminalScrollChange : undefined}
          />
        </div>
        {liveTmux && !termAtBottom ? (
          <button
            type="button"
            className="term-jump"
            onClick={onJumpToLive}
            aria-label="Jump to live cursor"
          >
            ↡ Jump to live
          </button>
        ) : null}
        {liveTmux ? (
          <TerminalScrollChips
            onWheel={onTerminalScrollChip}
            withJump={!termAtBottom}
          />
        ) : null}
      </div>
      {liveTmux ? (
        <TerminalKeyBar
          onSend={onTerminalInput}
          onRequestPaste={onRequestPaste}
          backend={session?.backend ?? null}
        />
      ) : null}
      {composeEnabled ? (
        <TerminalCompose
          host={host}
          token={token}
          sessionId={sessionId}
          session={session}
          onSubmit={onTerminalSubmit}
          onSubmitWithAttachments={onTerminalSubmitWithAttachments}
          attachmentsEnabled={attachmentsEnabled}
          onOpenFiles={onOpenFiles}
          onError={onError}
          expanded={composeOpen}
          onExpandedChange={setComposeOpen}
          connection={connection}
          rateLimitRefreshBusy={rateLimitRefreshBusy}
          onRateLimitRefresh={onRateLimitRefresh}
          refocusTerminal={refocusTerminal}
        />
      ) : null}
    </section>
  );
}

// Common terminal control bytes that are awkward to type on touch
// keyboards but routine for TUI agents. Each entry sends the literal
// bytes to the pane via the existing input handler — Ctrl-* combinations
// are encoded directly so the toolbar works even when the OS keyboard
// doesn't expose a Ctrl modifier. ``backends`` (when set) restricts a
// key to those backend ids; entries without it are universal. An entry
// with ``action`` instead of ``data`` dispatches a richer handler (e.g.
// reading the system clipboard) provided by the keybar host.
type KeyBarEntry = {
  label: string;
  title: string;
  backends?: string[];
  backendOnly?: boolean;
} & ({ data: string } | { action: "paste" });

const TERMINAL_KEY_BAR: KeyBarEntry[] = [
  { label: "Esc", data: "\x1b", title: "Escape" },
  { label: "Tab", data: "\t", title: "Tab" },
  {
    label: "⇧Tab",
    data: "\x1b[Z",
    title: "Shift-Tab (cycle modes)",
    // CC uses Shift-Tab for permission-mode cycling; Codex's TUI also
    // wires shift-tab → cycle mode in ``bottom_pane/footer.rs``. Other
    // backends don't use this key on their primary surface, so we
    // keep the chip out of their toolbar.
    backends: ["claude_code", "codex"],
    backendOnly: true,
  },
  { label: "Enter", data: "\r", title: "Enter (submit / select)" },
  // ``⇧↵`` instead of plain ``↵`` so the glyph reads as "the
  // shift-modified return key" — i.e. insert a newline in the composer
  // rather than submit. CC and Codex both wire Ctrl-J (\n) to a
  // soft-newline; the dedicated Enter chip above carries the submit
  // semantics.
  { label: "⇧↵", data: "\n", title: "Shift+Enter (newline)" },
  { label: "↑", data: "\x1b[A", title: "Up" },
  { label: "↓", data: "\x1b[B", title: "Down" },
  { label: "←", data: "\x1b[D", title: "Left" },
  { label: "→", data: "\x1b[C", title: "Right" },
  { label: "Paste", action: "paste", title: "Paste from clipboard" },
  { label: "^C", data: "\x03", title: "Ctrl-C (interrupt)" },
];

export function TerminalKeyBar({
  onSend,
  onRequestPaste,
  backend,
}: {
  onSend: (data: string) => void;
  onRequestPaste: () => void;
  backend: string | null | undefined;
}) {
  const keys = TERMINAL_KEY_BAR.filter(
    (key) => !key.backends || (backend ? key.backends.includes(backend) : false),
  );
  return (
    <div className="term-keys" role="group" aria-label="Send terminal keys">
      {keys.map((key) => (
        <button
          key={key.label}
          type="button"
          className={key.backendOnly ? "is-backend-only" : undefined}
          title={key.title}
          aria-label={key.title}
          // preventDefault on mousedown prevents the button from stealing
          // focus, which has two desirable effects: if xterm already had
          // focus the user can keep typing after the tap, and if focus
          // was elsewhere (e.g. the title editor or nowhere) it stays
          // put — tapping a key never pulls focus into the pane.
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            if ("action" in key && key.action === "paste") onRequestPaste();
            else if ("data" in key) onSend(key.data);
          }}
        >
          {key.label}
        </button>
      ))}
    </div>
  );
}

