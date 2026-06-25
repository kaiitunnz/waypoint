"use client";

import {
  type CSSProperties,
  Fragment,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { EventDiffPreview, EventDiffPreviewFile } from "@/lib/events";
import {
  buildPlainDiffLines,
  highlightDiffToLines,
  type DiffLine,
  type HighlightToken,
} from "@/lib/highlight";

// Below this container width the side-by-side layout is too cramped, so the
// diff defaults to inline until the user explicitly switches.
const SPLIT_MIN_WIDTH = 720;
// Unchanged lines kept adjacent to each change; longer runs collapse into an
// expandable fold (GitHub-style), so a small edit in a big file reads compactly.
const CONTEXT = 3;
const MIN_FOLD = 4;
// Lines revealed per directional expander click.
const EXPAND_STEP = 20;

type DiffLayout = "inline" | "split";

type DisplayItem =
  | { type: "line"; row: DiffLine }
  | { type: "fold"; key: string; count: number; isTop: boolean; isBottom: boolean };

type Segment = { type: "row"; index: number } | { type: "fold"; key: string; start: number; end: number };

interface FoldBounds {
  from: number;
  to: number;
}

interface WorkspaceDiffProps {
  preview: EventDiffPreview;
  path: string;
}

export function WorkspaceDiff({ preview, path }: WorkspaceDiffProps) {
  const file = preview.files[0];
  const containerRef = useRef<HTMLDivElement>(null);
  const [override, setOverride] = useState<DiffLayout | null>(null);
  const [wide, setWide] = useState(false);
  const [folds, setFolds] = useState<Record<string, FoldBounds>>({});

  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) setWide(entry.contentRect.width >= SPLIT_MIN_WIDTH);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const diffText = file?.diff ?? "";
  const plain = useMemo(() => (diffText ? buildPlainDiffLines(diffText) : []), [diffText]);
  const [lines, setLines] = useState<DiffLine[]>(plain);
  useEffect(() => {
    setLines(plain);
    setFolds({});
    if (!diffText) return;
    let cancelled = false;
    void highlightDiffToLines(diffText, path).then((highlighted) => {
      if (!cancelled && highlighted) setLines(highlighted);
    });
    return () => {
      cancelled = true;
    };
  }, [diffText, path, plain]);

  // Only actual file lines render; file/hunk headers are dropped so the view
  // reads as the whole file with changes marked.
  const rows = useMemo(
    () => lines.filter((line) => line.kind !== "meta" && line.kind !== "hunk"),
    [lines],
  );
  const gutter = useMemo(() => {
    let max = 1;
    for (const row of rows) {
      if (row.oldNo && row.oldNo > max) max = row.oldNo;
      if (row.newNo && row.newNo > max) max = row.newNo;
    }
    return `${String(max).length}ch`;
  }, [rows]);

  const segments = useMemo(() => computeSegments(rows), [rows]);
  const foldSpans = useMemo(() => {
    const map = new Map<string, Segment & { type: "fold" }>();
    for (const segment of segments) if (segment.type === "fold") map.set(segment.key, segment);
    return map;
  }, [segments]);

  const display = useMemo<DisplayItem[]>(() => {
    const out: DisplayItem[] = [];
    for (const segment of segments) {
      if (segment.type === "row") {
        out.push({ type: "line", row: rows[segment.index] });
        continue;
      }
      const bounds = folds[segment.key] ?? { from: segment.start, to: segment.end };
      for (let k = segment.start; k < bounds.from; k += 1) {
        out.push({ type: "line", row: rows[k] });
      }
      if (bounds.from < bounds.to) {
        out.push({
          type: "fold",
          key: segment.key,
          count: bounds.to - bounds.from,
          isTop: segment.start === 0,
          isBottom: segment.end === rows.length,
        });
      }
      for (let k = bounds.to; k < segment.end; k += 1) {
        out.push({ type: "line", row: rows[k] });
      }
    }
    return out;
  }, [segments, folds, rows]);

  const boundsFor = (key: string): FoldBounds => {
    const span = foldSpans.get(key);
    return folds[key] ?? { from: span?.start ?? 0, to: span?.end ?? 0 };
  };
  // "Show next lines" reveals from the top of the hidden region (lines flow down
  // from the content above); "Show previous lines" reveals from the bottom (the
  // lines just above the content below).
  const expandDown = (key: string) => {
    const b = boundsFor(key);
    setFolds((prev) => ({ ...prev, [key]: { from: Math.min(b.to, b.from + EXPAND_STEP), to: b.to } }));
  };
  const expandUp = (key: string) => {
    const b = boundsFor(key);
    setFolds((prev) => ({ ...prev, [key]: { from: b.from, to: Math.max(b.from, b.to - EXPAND_STEP) } }));
  };
  const expandAll = (key: string) => {
    const b = boundsFor(key);
    setFolds((prev) => ({ ...prev, [key]: { from: b.to, to: b.to } }));
  };
  const expanders = { onUp: expandUp, onDown: expandDown, onAll: expandAll };

  const layout: DiffLayout = override ?? (wide ? "split" : "inline");

  return (
    <div className="wp-diff" ref={containerRef}>
      <div className="wp-diff-toolbar">
        <span className="wp-diff-stat add">+{file?.additions ?? 0}</span>
        <span className="wp-diff-stat del">-{file?.deletions ?? 0}</span>
        {file?.truncated ? <span className="wp-diff-note">truncated</span> : null}
        <div className="wp-diff-layout">
          <button
            type="button"
            className={`wp-mode-btn${layout === "inline" ? " active" : ""}`}
            onClick={() => setOverride("inline")}
          >
            Inline
          </button>
          <button
            type="button"
            className={`wp-mode-btn${layout === "split" ? " active" : ""}`}
            onClick={() => setOverride("split")}
          >
            Split
          </button>
        </div>
      </div>
      <DiffBody
        file={file}
        rows={rows}
        display={display}
        layout={layout}
        gutter={gutter}
        expanders={expanders}
      />
    </div>
  );
}

interface Expanders {
  onUp: (key: string) => void;
  onDown: (key: string) => void;
  onAll: (key: string) => void;
}

function DiffBody({
  file,
  rows,
  display,
  layout,
  gutter,
  expanders,
}: {
  file: EventDiffPreviewFile | undefined;
  rows: DiffLine[];
  display: DisplayItem[];
  layout: DiffLayout;
  gutter: string;
  expanders: Expanders;
}) {
  if (!file) {
    return <div className="wp-diff-empty">No changes against HEAD</div>;
  }
  if (file.unavailableReason || (!file.diff && !rows.length)) {
    return (
      <div className="wp-diff-empty">
        {file.binary
          ? "Binary file — open the Content tab."
          : (file.unavailableReason ?? "No diff content available.")}
      </div>
    );
  }
  return layout === "split" ? (
    <SplitDiff display={display} gutter={gutter} expanders={expanders} />
  ) : (
    <InlineDiff display={display} gutter={gutter} expanders={expanders} />
  );
}

function InlineDiff({
  display,
  gutter,
  expanders,
}: {
  display: DisplayItem[];
  gutter: string;
  expanders: Expanders;
}) {
  return (
    <div className="wp-diff-inline wp-hl" style={{ "--lnw": gutter } as CSSProperties}>
      {display.map((item, index) =>
        item.type === "fold" ? (
          <FoldRow key={`f${item.key}`} item={item} expanders={expanders} />
        ) : (
          <div className={`wp-diff-row ${item.row.kind}`} key={index}>
            <span className="wp-diff-ln">{item.row.oldNo ?? ""}</span>
            <span className="wp-diff-ln">{item.row.newNo ?? ""}</span>
            <span className="wp-diff-sign">
              {item.row.kind === "add" ? "+" : item.row.kind === "del" ? "-" : " "}
            </span>
            <span className="wp-diff-code">{renderCode(item.row.tokens)}</span>
          </div>
        ),
      )}
    </div>
  );
}

function SplitDiff({
  display,
  gutter,
  expanders,
}: {
  display: DisplayItem[];
  gutter: string;
  expanders: Expanders;
}) {
  const nodes: ReactNode[] = [];
  let run: DiffLine[] = [];
  let runStart = 0;
  const flush = () => {
    if (!run.length) return;
    buildSplitPairs(run).forEach((pair, i) => {
      nodes.push(
        <div className="wp-diff-srow" key={`s${runStart}-${i}`}>
          <span className={`wp-diff-ln ${pair.left ? pair.left.kind : "blank"}`}>
            {pair.left?.oldNo ?? ""}
          </span>
          <span className={`wp-diff-scode ${pair.left ? pair.left.kind : "blank"}`}>
            {pair.left ? renderCode(pair.left.tokens) : ""}
          </span>
          <span className={`wp-diff-ln new ${pair.right ? pair.right.kind : "blank"}`}>
            {pair.right?.newNo ?? ""}
          </span>
          <span className={`wp-diff-scode ${pair.right ? pair.right.kind : "blank"}`}>
            {pair.right ? renderCode(pair.right.tokens) : ""}
          </span>
        </div>,
      );
    });
    run = [];
  };
  display.forEach((item, index) => {
    if (item.type === "line") {
      if (!run.length) runStart = index;
      run.push(item.row);
    } else {
      flush();
      nodes.push(<FoldRow split key={`f${item.key}`} item={item} expanders={expanders} />);
    }
  });
  flush();
  return (
    <div className="wp-diff-split wp-hl" style={{ "--lnw": gutter } as CSSProperties}>
      {nodes}
    </div>
  );
}

// A single position-aware fold bar per gap: directional step buttons for the
// available direction(s) — ⤓ reveals downward from the block above, ⤒ reveals
// upward from the block below — a line count, and one Expand all. A short gap is
// a single full-width expand control.
function FoldRow({
  item,
  split,
  expanders,
}: {
  item: { key: string; count: number; isTop: boolean; isBottom: boolean };
  split?: boolean;
  expanders: Expanders;
}) {
  const { key, count, isTop, isBottom } = item;
  const className = `wp-diff-fold${split ? " split" : ""}`;

  if (count <= EXPAND_STEP) {
    return (
      <div className={className}>
        <button
          type="button"
          className="wp-diff-fold-solo"
          onClick={() => expanders.onAll(key)}
        >
          <span className="wp-diff-fold-icon" aria-hidden="true">
            ⌄
          </span>
          Expand {count} unchanged {count === 1 ? "line" : "lines"}
        </button>
      </div>
    );
  }

  return (
    <div className={className}>
      <div className="wp-diff-fold-steps">
        {!isTop ? (
          <button
            type="button"
            className="wp-diff-fold-step"
            title="Show next lines"
            aria-label="Show next lines"
            onClick={() => expanders.onDown(key)}
          >
            ⌄
          </button>
        ) : null}
        {!isBottom ? (
          <button
            type="button"
            className="wp-diff-fold-step"
            title="Show previous lines"
            aria-label="Show previous lines"
            onClick={() => expanders.onUp(key)}
          >
            ⌃
          </button>
        ) : null}
      </div>
      <span className="wp-diff-fold-label">{count} unchanged lines</span>
      <button
        type="button"
        className="wp-diff-fold-all"
        onClick={() => expanders.onAll(key)}
      >
        Expand all
      </button>
    </div>
  );
}

interface SplitPair {
  left: DiffLine | null;
  right: DiffLine | null;
}

// Pair deletions with the additions that replace them so a change block lines up
// side by side; context lines mirror on both sides.
function buildSplitPairs(rows: DiffLine[]): SplitPair[] {
  const pairs: SplitPair[] = [];
  let index = 0;
  while (index < rows.length) {
    if (rows[index].kind === "context") {
      pairs.push({ left: rows[index], right: rows[index] });
      index += 1;
      continue;
    }
    const dels: DiffLine[] = [];
    const adds: DiffLine[] = [];
    while (index < rows.length && rows[index].kind === "del") dels.push(rows[index++]);
    while (index < rows.length && rows[index].kind === "add") adds.push(rows[index++]);
    const span = Math.max(dels.length, adds.length);
    for (let k = 0; k < span; k += 1) {
      pairs.push({ left: dels[k] ?? null, right: adds[k] ?? null });
    }
  }
  return pairs;
}

// Keep CONTEXT unchanged lines around every change and mark longer runs of
// untouched context (including the file's head and tail) as folds.
function computeSegments(rows: DiffLine[]): Segment[] {
  const near = rows.map(() => false);
  rows.forEach((row, i) => {
    if (row.kind === "context") return;
    for (let j = Math.max(0, i - CONTEXT); j <= Math.min(rows.length - 1, i + CONTEXT); j += 1) {
      near[j] = true;
    }
  });
  const segments: Segment[] = [];
  let i = 0;
  while (i < rows.length) {
    if (near[i] || rows[i].kind !== "context") {
      segments.push({ type: "row", index: i });
      i += 1;
      continue;
    }
    let j = i;
    while (j < rows.length && !near[j] && rows[j].kind === "context") j += 1;
    if (j - i >= MIN_FOLD) {
      segments.push({ type: "fold", key: `fold-${i}`, start: i, end: j });
    } else {
      for (let k = i; k < j; k += 1) segments.push({ type: "row", index: k });
    }
    i = j;
  }
  return segments;
}

function renderCode(tokens: HighlightToken[]): ReactNode {
  if (!tokens.length || tokens.every((token) => token.text === "")) return " ";
  return tokens.map((token, i) =>
    token.className ? (
      <span key={i} className={token.className}>
        {token.text}
      </span>
    ) : (
      <Fragment key={i}>{token.text}</Fragment>
    ),
  );
}
