"use client";

import { CSSProperties, useLayoutEffect, useRef, useState } from "react";

interface ExpandableTextProps {
  text: string;
  className?: string;
  collapsedMaxHeight?: string;
  expandedMaxHeight?: string;
  moreLabel?: string;
  lessLabel?: string;
}

// Text that clamps to a few lines with a fade and reveals a "Show more" toggle
// only when it actually overflows — the same affordance the side-question dock
// uses for long aside answers, so long scheduled messages read consistently.
export function ExpandableText({
  text,
  className,
  collapsedMaxHeight = "4.5em",
  expandedMaxHeight = "34vh",
  moreLabel = "Show more",
  lessLabel = "Show less",
}: ExpandableTextProps) {
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const ref = useRef<HTMLParagraphElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || expanded) {
      return;
    }
    setOverflowing(el.scrollHeight - el.clientHeight > 4);
  }, [text, expanded]);

  const style: CSSProperties = {
    maxHeight: expanded ? expandedMaxHeight : collapsedMaxHeight,
  };

  return (
    <>
      <p
        ref={ref}
        style={style}
        className={[
          "expandable-text",
          className ?? "",
          expanded
            ? "expandable-text-expanded"
            : `expandable-text-clamped${overflowing ? " expandable-text-fade" : ""}`,
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {text}
      </p>
      {overflowing || expanded ? (
        <button
          type="button"
          className="expandable-more"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? lessLabel : moreLabel}
        </button>
      ) : null}
    </>
  );
}
