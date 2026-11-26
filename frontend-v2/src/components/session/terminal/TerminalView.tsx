"use client";

import { useCallback, useState, type ReactNode, type Ref } from "react";

import type { TerminalPane } from "@waypoint/web-core/hooks/useTerminalPane";
import { wheelSequence, type ScrollDirection, type TerminalDims } from "@waypoint/web-core/session/terminal";

import type { ReconnectAction } from "@/components/session/screenMenus";

import { TerminalBar } from "./TerminalBar";
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
  const { term, input, resize, appearance, interactive, keyInjection, resizable, lockable, unlocked, typable, toggleLock } = pane;
  const [dims, setDims] = useState<TerminalDims | null>(null);
  const [atBottom, setAtBottom] = useState(true);

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
        onJumpToLive={
          interactive && !atBottom
            ? (hadFocus) => {
                term.current?.scrollToBottom();
                if (hadFocus) term.current?.focus();
              }
            : null
        }
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
    </section>
  );
}
