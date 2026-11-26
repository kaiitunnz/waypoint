"use client";

import { useCallback, useState, type ReactNode, type Ref } from "react";

import { useTerminalPaste } from "@waypoint/web-core/hooks/useTerminalPaste";
import { wheelSequence, type ScrollDirection, type TerminalDims } from "@waypoint/web-core/session/terminal";

import { useToast } from "@/components/ui/Toast";

import { PastePrompt } from "./PastePrompt";
import { TerminalBar } from "./TerminalBar";
import { TerminalKeyBar } from "./TerminalKeyBar";
import { TerminalStage } from "./TerminalStage";
import type { TerminalPane } from "./useTerminalPane";
import styles from "./TerminalView.module.css";

interface TerminalViewProps {
  pane: TerminalPane;
  exited: boolean;
  // The session stopped and can be brought back.
  reconnect: { busy: boolean; run: () => void } | null;
  lead?: ReactNode;
  menu: ReactNode;
  // Takes the focus back from a control that goes away.
  ref?: Ref<HTMLElement>;
}

// The Terminal view of the main pane: the term bar, the grid and the key bar.
// The footer below it holds the docks and the composer.
export function TerminalView({ pane, exited, reconnect, lead, menu, ref }: TerminalViewProps) {
  const { toast } = useToast();
  const { term, connection, interactive, keyInjection, resizable, lockable, unlocked, typable, toggleLock } = pane;
  const [dims, setDims] = useState<TerminalDims | null>(null);
  const [atBottom, setAtBottom] = useState(true);

  const send = connection.input;
  const input = useCallback((data: string) => void send(data), [send]);
  const reportError = useCallback((message: string) => toast({ message, tone: "error" }), [toast]);
  const paste = useTerminalPaste(input, reportError);
  const resize = connection.resize;
  const onResize = useCallback(
    (size: TerminalDims) => {
      setDims(size);
      resize(size.cols, size.rows);
    },
    [resize],
  );
  const scroll = useCallback((direction: ScrollDirection) => input(wheelSequence(direction, dims)), [input, dims]);

  return (
    <section ref={ref} tabIndex={-1} className={styles.view} aria-label="Terminal">
      <TerminalBar
        lead={lead}
        lock={lockable && !exited ? { unlocked, onToggle: toggleLock } : null}
        onScroll={keyInjection ? scroll : null}
        onJumpToLive={interactive && !atBottom ? () => term.current?.scrollToBottom() : null}
        dims={interactive ? dims : null}
        reconnect={reconnect}
        menu={menu}
      />
      <TerminalStage
        key={resizable ? "fit" : "fixed"}
        termRef={term}
        fit={resizable}
        typable={typable}
        appearance={connection.appearance}
        onData={input}
        onResize={onResize}
        onScrollChange={setAtBottom}
      />
      {keyInjection || paste.pending !== null ? (
        <div className={styles.foot}>
          <div className={styles.col}>
            {paste.pending !== null ? (
              <PastePrompt
                key={paste.seq}
                length={paste.pending.length}
                onSend={paste.sendPending}
                onDismiss={paste.dismiss}
              />
            ) : null}
            {keyInjection ? <TerminalKeyBar onSend={input} onPaste={() => void paste.request()} /> : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
