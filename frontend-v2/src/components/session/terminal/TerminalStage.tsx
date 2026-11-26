"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import "@xterm/xterm/css/xterm.css";

import { useXTerminal, type XTerminalHandle } from "@waypoint/web-core/hooks/useXTerminal";
import type { TerminalAppearance } from "@waypoint/web-core/session/terminal";

import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";
import { terminalFontSize, terminalThemeFor } from "@/lib/terminalTheme";

import styles from "./TerminalStage.module.css";

interface TerminalStageProps {
  termRef: RefObject<XTerminalHandle | null>;
  // Off for a grid the server owns: it keeps its native size and the stage
  // scrolls it. Fixed for the stage's life.
  fit: boolean;
  typable: boolean;
  appearance: TerminalAppearance;
  onData: (data: string) => void;
  onResize: (size: { cols: number; rows: number }) => void;
  onScrollChange: (atBottom: boolean) => void;
}

interface Pan {
  // The thumb's offset and length, as fractions of the track.
  start: number;
  size: number;
  atEnd: boolean;
}

export function TerminalStage({ termRef, fit, typable, appearance, onData, onResize, onScrollChange }: TerminalStageProps) {
  const pointer = usePointerUI();
  const host = useRef<HTMLDivElement>(null);
  // The token is per input; a change of input renders this again.
  const fontSize = terminalFontSize();
  const handle = useXTerminal(host, {
    readOnly: !typable,
    autoFit: fit,
    fontSize,
    themeFor: terminalThemeFor,
    appearance,
    onData: typable ? onData : undefined,
    onResize,
    onScrollChange,
  });
  useEffect(() => {
    termRef.current = handle;
    return () => {
      if (termRef.current === handle) termRef.current = null;
    };
  }, [termRef, handle]);

  // A grid wider than a phone pans sideways, with a thin indicator.
  const [pan, setPan] = useState<Pan | null>(null);
  const measure = useCallback(() => {
    const node = host.current;
    if (!node || fit || pointer) return;
    const { scrollLeft, scrollWidth, clientWidth } = node;
    if (scrollWidth <= clientWidth + 1) {
      setPan(null);
      return;
    }
    setPan({
      start: scrollLeft / scrollWidth,
      size: clientWidth / scrollWidth,
      atEnd: scrollLeft + clientWidth >= scrollWidth - 1,
    });
  }, [fit, pointer]);
  useEffect(() => {
    const node = host.current;
    if (!node || fit || pointer) {
      setPan(null);
      return;
    }
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    const grid = node.firstElementChild;
    if (grid) observer.observe(grid);
    return () => observer.disconnect();
  }, [fit, pointer, measure]);

  return (
    <div
      className={cx(styles.stage, !fit && (pointer ? styles.letterbox : styles.pan))}
      data-appearance={appearance}
    >
      <div
        ref={host}
        className={cx(styles.host, fit ? styles.fit : styles.fixed, pan && !pan.atEnd && styles.fade)}
        onScroll={pan || (!fit && !pointer) ? measure : undefined}
      />
      {pan ? (
        <div className={styles.track} aria-hidden="true">
          <i style={{ left: `${pan.start * 100}%`, width: `${pan.size * 100}%` }} />
        </div>
      ) : null}
    </div>
  );
}
