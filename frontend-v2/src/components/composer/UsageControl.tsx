"use client";

import { useRef, useState } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { USAGE_POPOVER_SIZE_KEY, usagePanelModel } from "@waypoint/web-core/session/usagePanel";
import type { SessionRecord } from "@waypoint/web-core/types";

import { Flyout } from "@/components/ui/Flyout";
import { cx } from "@/lib/cx";

import styles from "./Usage.module.css";
import { UsagePanel } from "./UsagePanel";
import { useUsageRefresh } from "./useUsageRefresh";

const RADIUS = 7;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

interface UsageControlProps {
  session: SessionRecord | null;
  catalog: BackendCatalog;
  onSession: (session: SessionRecord) => void;
}

// A session without usage data has no ring.
export function UsageControl({ session, catalog, onSession }: UsageControlProps) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const model = usagePanelModel(session, catalog);
  const { refresh, refreshing } = useUsageRefresh(session, onSession);

  if (!model.hasUsage) return null;
  const { contextPercent: percent, tone } = model;
  const summary = model.summary.join(". ");

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={cx(styles.ring, styles[tone])}
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
        resizeKey={USAGE_POPOVER_SIZE_KEY}
      >
        <UsagePanel model={model} catalog={catalog} onRefresh={() => void refresh()} refreshing={refreshing} />
      </Flyout>
    </>
  );
}
