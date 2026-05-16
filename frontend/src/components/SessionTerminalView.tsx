"use client";

import { Dispatch, MutableRefObject, SetStateAction } from "react";

import { TerminalScrollChips } from "@/components/TerminalScrollChips";
import { XTerminal, type XTerminalHandle } from "@/components/XTerminal";
import { SessionRecord } from "@/lib/types";

// Visual + textual parity with the composer-overflow menu on the chat
// page — same trigger glyph, same labels, same per-row glyph slots and
// danger styling. Keeps the two surfaces feeling like one app.

interface SessionTerminalViewProps {
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
  // True on terminal-only (tmux) sessions where no composer follows — the
  // pane sticky-locks to the viewport bottom once the user scrolls past
  // the SessionHeader. Chat-capable Terminal tabs stay bounded so the
  // composer beneath remains visible.
  locked: boolean;
  onTerminalInput: (data: string) => void;
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
}

export function SessionTerminalView({
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
  locked,
  onTerminalInput,
  onTerminalResize,
  onTerminalScrollChip,
  onTerminalScrollChange,
  onJumpToLive,
  onRefresh,
  onReattach,
  onTerminate,
  onRemoveFromList,
  onSwitchSession,
}: SessionTerminalViewProps) {
  // Primary action shown as a pill button next to the overflow trigger.
  // Only states the user can't trivially reach inside the pane — Reconnect
  // when exited (the pane is gone) and Refresh for read-only snapshots.
  // We deliberately do NOT surface "Interrupt" here because the keybar's
  // ^C already sends SIGINT to the pane (the backend /interrupt action
  // for tmux just writes ^C anyway).
  let primary: { label: string; onClick: () => void; tone?: "primary" } | null = null;
  if (dormantReattach) {
    primary = { label: "Reconnect", onClick: () => void onReattach(), tone: "primary" };
  } else if (!liveTmux && session) {
    primary = {
      label: snapshotLoading ? "Refreshing…" : "Refresh",
      onClick: onRefresh,
    };
  }

  const closeMenu = () => setTermMenuOpen(false);
  const fireFromMenu = (cb: () => void | Promise<void>) => {
    closeMenu();
    void cb();
  };

  return (
    <section
      className={`session-terminal ${locked ? "is-locked" : ""}`}
      aria-label="Terminal session"
    >
      <div className="term-bar">
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
            disabled={snapshotLoading && primary.label.startsWith("Refresh")}
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
              {session && primary?.label !== "Refresh" ? (
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
              {dormantReattach && primary?.label !== "Reconnect" ? (
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
          backend={session?.backend ?? null}
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
// key to those backend ids; entries without it are universal.
const TERMINAL_KEY_BAR: {
  label: string;
  data: string;
  title: string;
  backends?: string[];
  backendOnly?: boolean;
}[] = [
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
  { label: "↵", data: "\n", title: "Newline (Ctrl-J)" },
  { label: "↑", data: "\x1b[A", title: "Up" },
  { label: "↓", data: "\x1b[B", title: "Down" },
  { label: "←", data: "\x1b[D", title: "Left" },
  { label: "→", data: "\x1b[C", title: "Right" },
  { label: "^C", data: "\x03", title: "Ctrl-C (interrupt)" },
];

export function TerminalKeyBar({
  onSend,
  backend,
}: {
  onSend: (data: string) => void;
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
          onClick={() => onSend(key.data)}
        >
          {key.label}
        </button>
      ))}
    </div>
  );
}

