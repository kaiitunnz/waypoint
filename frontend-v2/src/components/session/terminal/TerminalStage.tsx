"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";

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

// The letterbox's inset around a grid that fits; `.letterbox .fixed` pads by it.
const LETTERBOX_INSET = 12;

const samePan = (a: Pan | null, b: Pan | null) =>
  a === b ||
  (a !== null &&
    b !== null &&
    a.atEnd === b.atEnd &&
    Math.abs(a.start - b.start) < 0.001 &&
    Math.abs(a.size - b.size) < 0.001);

export function TerminalStage({ termRef, fit, typable, appearance, onData, onResize, onScrollChange }: TerminalStageProps) {
  const pointer = usePointerUI();
  const host = useRef<HTMLDivElement>(null);
  // The size token is per input, so it is read again when the input changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const font = useMemo(() => ({ size: terminalFontSize(), family: terminalFontFamily() }), [pointer]);
  const handle = useXTerminal(host, {
    readOnly: !typable,
    autoFit: fit,
    fontSize: font.size,
    fontFamily: font.family,
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

  // A grid that fits with its inset sits on a letterbox; a wider one pans, and
  // on touch, where scrollbars hide, shows a thin indicator. The mode follows
  // the grid's own width, which neither mode's padding changes.
  const [pan, setPan] = useState<Pan | null>(null);
  const measure = useCallback(() => {
    const node = host.current;
    const grid = node?.firstElementChild;
    if (!node || !(grid instanceof HTMLElement)) return;
    const { scrollLeft, scrollWidth, clientWidth } = node;
    const next =
      grid.offsetWidth + 2 * LETTERBOX_INSET > clientWidth
        ? {
            start: scrollLeft / scrollWidth,
            size: Math.min(1, clientWidth / scrollWidth),
            atEnd: scrollLeft + clientWidth >= scrollWidth - 1,
          }
        : null;
    setPan((current) => (samePan(current, next) ? current : next));
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
  const indicator = pan && pan.size < 1 && !pointer ? pan : null;

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
