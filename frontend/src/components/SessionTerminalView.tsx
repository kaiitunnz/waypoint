"use client";

import { Dispatch, MutableRefObject, SetStateAction, useCallback, useState } from "react";
import { createPortal } from "react-dom";

import { SessionUsagePill } from "@/components/SessionUsagePill";
import { TerminalCompose } from "@/components/TerminalCompose";
import { TerminalScrollChips } from "@/components/TerminalScrollChips";
import { XTerminal, type XTerminalHandle } from "@/components/XTerminal";
import { terminalResizable, useBackendCatalog } from "@/lib/backends";
import type { TerminalSubmitResult } from "@/lib/composer";
import { SessionRecord } from "@/lib/types";
import { usePopoverAnchor } from "@/lib/use-popover-anchor";

type Connection = "idle" | "connecting" | "open" | "reconnecting";

interface SessionTerminalViewProps {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  interactive: boolean;
  // The pane accepts key-bar / scroll injection even when not fully
  // interactive (claude_tty). Implied by, and broader than, ``interactive``.
  keyInjection: boolean;
  terminalRef: MutableRefObject<XTerminalHandle | null>;
  terminalDims: { cols: number; rows: number } | null;
  // Light/dark surface for the pane, resolved from the agent's TUI theme.
  terminalAppearance: "light" | "dark";
  sessionExited: boolean;
  dormantReattach: boolean;
  termMenuOpen: boolean;
  setTermMenuOpen: Dispatch<SetStateAction<boolean>>;
  termMenuWrapRef: MutableRefObject<HTMLDivElement | null>;
  termAtBottom: boolean;
  connection: Connection;
  rateLimitRefreshBusy: boolean;
  onRateLimitRefresh: () => void | Promise<void>;
  // Presents the pane fullscreen: it sticky-locks to the viewport and fills it
  // (minus any composer) once the user scrolls past the SessionHeader. On for
  // the Terminal tab of every session so emulated panes match a terminal-only
  // session's height; the is-locked rule reserves the composer when present.
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
  onRequestPaste: () => void;
  onTerminalResize: (size: { cols: number; rows: number }) => void;
  onTerminalScrollChip: (direction: "up" | "down") => void;
  onTerminalScrollChange: (atBottom: boolean) => void;
  onJumpToLive: () => void;
  // Refresh reconnects the terminal WS, re-seeding the pane from the server.
  onRefresh: () => void;
  onResume: () => void | Promise<void>;
  onReattach: () => void | Promise<void>;
  onTerminate: () => void | Promise<void>;
  onRemoveFromList: () => void | Promise<void>;
  onSwitchSession: () => void;
  onOpenSettings: () => void;
  onError: (message: string) => void;
}

export function SessionTerminalView({
  host,
  token,
  sessionId,
  session,
  interactive,
  keyInjection,
  terminalRef,
  terminalDims,
  terminalAppearance,
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
  onRequestPaste,
  onTerminalResize,
  onTerminalScrollChip,
  onTerminalScrollChange,
  onJumpToLive,
  onRefresh,
  onResume,
  onReattach,
  onTerminate,
  onRemoveFromList,
  onSwitchSession,
  onOpenSettings,
  onError,
}: SessionTerminalViewProps) {
  const catalog = useBackendCatalog(host || null, token || null, null);
  // Emulated panes (claude_tty) are pinned to a fixed server-side grid. The
  // terminal must mirror that grid exactly rather than fit to the viewport,
  // or the cell-positioned stream misaligns; the host scrolls at native size.
  const fixedGrid = session ? !terminalResizable(session.transport, catalog) : false;
  // Primary action shown as a pill button next to the overflow trigger.
  // Only states the user can't trivially reach inside the pane — Reconnect
  // when exited (the pane is gone). We deliberately do NOT surface "Interrupt"
  // here because the keybar's ^C already sends SIGINT to the pane.
  type PrimaryAction = {
    kind: "reconnect";
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
  }

  const closeMenu = () => setTermMenuOpen(false);
  const fireFromMenu = (cb: () => void | Promise<void>) => {
    closeMenu();
    void cb();
  };

  const [composeOpen, setComposeOpen] = useState(false);
  // The compose drawer collapses back to a hairline handle when the
  // session isn't interactive, so we don't carry stale "open" state across a
  // disconnect / view switch.
  const composeEnabled = interactive;
  const refocusTerminal = useCallback(() => {
    terminalRef.current?.focus();
  }, [terminalRef]);
  // The menu is portaled to ``document.body`` for the same reason as the
  // usage panel — it drops out of the term-bar, and ``.session-terminal``'s
  // ``overflow: hidden`` would clip an in-pane absolute menu. Right-anchored
  // to match the trigger's top-right home.
  const overflowMenuStyle = usePopoverAnchor(termMenuWrapRef, termMenuOpen, "right");

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
          catalog={catalog}
          onRateLimitRefresh={onRateLimitRefresh}
          rateLimitRefreshBusy={rateLimitRefreshBusy}
          anchored
        />
        <span className="term-bar-spacer" />
        {interactive && terminalDims ? (
          <span className="term-bar-dims" aria-label="Pane dimensions">
            {terminalDims.cols}×{terminalDims.rows}
          </span>
        ) : null}
        {primary ? (
          <button
            type="button"
            className={`term-bar-action ${primary.tone === "primary" ? "primary" : ""}`}
            onClick={primary.onClick}
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
          {termMenuOpen && typeof document !== "undefined" ? (
            createPortal(
            <div
              className="composer-overflow-menu"
              role="menu"
              data-term-overflow-menu
              style={overflowMenuStyle ?? undefined}
            >
              {session ? (
                <button
                  type="button"
                  role="menuitem"
                  className="composer-overflow-item"
                  onClick={() => {
                    closeMenu();
                    onRefresh();
                  }}
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
              {locked && !sessionExited && session?.status !== "running" ? (
                <button
                  type="button"
                  role="menuitem"
                  className="composer-overflow-item"
                  onClick={() => fireFromMenu(onResume)}
                >
                  <span className="glyph">⟳</span>
                  Resume pane
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
              {session ? (
                <button
                  type="button"
                  role="menuitem"
                  className="composer-overflow-item"
                  onClick={() => {
                    closeMenu();
                    onOpenSettings();
                  }}
                >
                  <span className="glyph">⚙︎</span>
                  Session settings…
                </button>
              ) : null}
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
            </div>,
            document.body,
            )
          ) : null}
        </div>
      </div>
      <div
        className={`term-stage${
          terminalAppearance === "light" ? " term-stage--light" : ""
        }`}
      >
        <div className="term-stage-host" role="log" aria-live="polite">
          <XTerminal
            // Remount when the transport (and thus fit mode) changes — autoFit
            // is fixed at mount, so switching sessions must rebuild the term.
            key={session?.transport ?? "none"}
            ref={terminalRef}
            readOnly={!interactive}
            autoFit={!fixedGrid}
            appearance={terminalAppearance}
            onData={interactive ? onTerminalInput : undefined}
            onResize={onTerminalResize}
            onScrollChange={interactive ? onTerminalScrollChange : undefined}
          />
        </div>
        {interactive && !termAtBottom ? (
          <button
            type="button"
            className="term-jump"
            onClick={onJumpToLive}
            aria-label="Jump to live cursor"
          >
            ↡ Jump to live
          </button>
        ) : null}
        {keyInjection ? (
          <TerminalScrollChips
            onWheel={onTerminalScrollChip}
            // Lift the cluster only when the "jump to live" pill shares the
            // corner — that pill is interactive-only (see above).
            withJump={interactive && !termAtBottom}
          />
        ) : null}
      </div>
      {keyInjection ? (
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
          onSubmit={onTerminalSubmit}
          onSubmitWithAttachments={onTerminalSubmitWithAttachments}
          attachmentsEnabled={attachmentsEnabled}
          onError={onError}
          expanded={composeOpen}
          onExpandedChange={setComposeOpen}
          connection={connection}
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

