"use client";

import { useCallback, useState, type ReactNode, type Ref } from "react";

import type { TerminalPane } from "@waypoint/web-core/hooks/useTerminalPane";
import { useTerminalPaste } from "@waypoint/web-core/hooks/useTerminalPaste";
import { wheelSequence, type ScrollDirection, type TerminalDims } from "@waypoint/web-core/session/terminal";

import { useToast } from "@/components/ui/Toast";
import type { ReconnectAction } from "@/components/session/screenMenus";

import { PastePrompt } from "./PastePrompt";
import { TerminalBar } from "./TerminalBar";
import { TerminalKeyBar } from "./TerminalKeyBar";
import { TerminalStage } from "./TerminalStage";
import styles from "./TerminalView.module.css";

interface TerminalViewProps {
  pane: TerminalPane;
  exited: boolean;
  reconnect: ReconnectAction | null;
  lead?: ReactNode;
  menu: ReactNode;
  // Takes the focus back from a control that goes away.
  ref?: Ref<HTMLElement>;
}

export function TerminalView({ pane, exited, reconnect, lead, menu, ref }: TerminalViewProps) {
  const { toast } = useToast();
  const { term, input, resize, appearance, interactive, keyInjection, resizable, lockable, unlocked, typable, toggleLock } = pane;
  const [dims, setDims] = useState<TerminalDims | null>(null);
  const [atBottom, setAtBottom] = useState(true);

  const reportError = useCallback((message: string) => toast({ message, tone: "error" }), [toast]);
  const paste = useTerminalPaste(input, reportError);
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
        appearance={appearance}
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
