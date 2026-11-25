"use client";

import { useRef, useState } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { usagePanelModel } from "@waypoint/web-core/session/usagePanel";
import type { SessionRecord } from "@waypoint/web-core/types";

import { Flyout } from "@/components/ui/Flyout";
import { cx } from "@/lib/cx";

import hit from "@/styles/hit.module.css";
import styles from "./Usage.module.css";
import { UsagePanel } from "./UsagePanel";

const RADIUS = 7;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

interface UsageControlProps {
  session: SessionRecord | null;
  catalog: BackendCatalog;
  onRefresh: () => void;
  refreshing: boolean;
}

// The context ring and the usage panel it opens. A session without usage data
// has neither.
export function UsageControl({ session, catalog, onRefresh, refreshing }: UsageControlProps) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const model = usagePanelModel(session, catalog);
  if (!session || !model.hasUsage) return null;
  const { contextPercent: percent, tone } = model;
  const summary = [
    model.contextSummary ? `Current context ${model.contextSummary}` : null,
    model.rateLimitSummary ? `Rate limits ${model.rateLimitSummary}` : null,
  ]
    .filter(Boolean)
    .join(". ");

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={cx(styles.ring, styles[tone], hit.hit)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={summary ? `Usage. ${summary}` : "Usage"}
        title={summary || "Usage"}
        onClick={() => setOpen((value) => !value)}
      >
        <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
          <circle className={styles.track} cx="9" cy="9" r={RADIUS} />
          {percent !== null ? (
            <circle
              className={styles.fill}
              cx="9"
              cy="9"
              r={RADIUS}
              strokeDasharray={`${(percent / 100) * CIRCUMFERENCE} ${CIRCUMFERENCE}`}
              transform="rotate(-90 9 9)"
            />
          ) : null}
        </svg>
        {percent !== null ? <span>{percent}%</span> : null}
      </button>
      <Flyout
        open={open}
        onClose={() => setOpen(false)}
        triggerRef={trigger}
        title="Usage"
        width={380}
        side="top"
        align="right"
      >
        <UsagePanel model={model} catalog={catalog} onRefresh={onRefresh} refreshing={refreshing} />
      </Flyout>
    </>
  );
}
