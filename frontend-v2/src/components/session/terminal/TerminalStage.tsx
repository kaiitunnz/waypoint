"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import "@xterm/xterm/css/xterm.css";

import { useXTerminal, type XTerminalHandle } from "@waypoint/web-core/hooks/useXTerminal";
import type { TerminalAppearance } from "@waypoint/web-core/session/terminal";

import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";
import { terminalFontFamily, terminalFontSize, terminalThemeFor } from "@/lib/terminalTheme";

import styles from "./TerminalStage.module.css";

interface TerminalStageProps {
  termRef: RefObject<XTerminalHandle | null>;
  // Read at mount; the caller remounts the stage when it changes.
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
    fontFamily: terminalFontFamily(),
    themeFor: terminalThemeFor,
    onData,
    onResize,
    onScrollChange,
  });
  useEffect(() => {
    termRef.current = handle;
    return () => {
      if (termRef.current === handle) termRef.current = null;
    };
  }, [termRef, handle]);

  // A grid that fits sits on a letterbox; a wider one pans, and on touch,
  // where scrollbars hide, shows a thin indicator.
  const [pan, setPan] = useState<Pan | null>(null);
  const measure = useCallback(() => {
    const node = host.current;
    if (!node) return;
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
  }, []);
  useEffect(() => {
    const node = host.current;
    if (!node || fit) return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    const grid = node.firstElementChild;
    if (grid) observer.observe(grid);
    return () => observer.disconnect();
  }, [fit, measure]);
  const indicator = pan && !pointer ? pan : null;

  return (
    <div
      className={cx(styles.stage, !fit && (pan ? styles.pan : styles.letterbox))}
      data-appearance={appearance}
    >
      <div
        ref={host}
        className={cx(styles.host, fit ? styles.fit : styles.fixed, indicator && !indicator.atEnd && styles.fade)}
        onScroll={fit ? undefined : measure}
      />
      {indicator ? (
        <div className={styles.track} aria-hidden="true">
          <i style={{ left: `${indicator.start * 100}%`, width: `${indicator.size * 100}%` }} />
        </div>
      ) : null}
    </div>
  );
}
