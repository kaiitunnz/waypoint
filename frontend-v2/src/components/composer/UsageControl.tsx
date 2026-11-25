"use client";

import { useRef, useState } from "react";

import { refreshSessionRateLimitUsage } from "@waypoint/web-core/api";
import type { BackendCatalog } from "@waypoint/web-core/backends";
import { USAGE_POPOVER_SIZE_KEY } from "@waypoint/web-core/popoverSize";
import { usagePanelModel } from "@waypoint/web-core/session/usagePanel";
import type { SessionRecord } from "@waypoint/web-core/types";

import { Flyout } from "@/components/ui/Flyout";
import { cx } from "@/lib/cx";
import { useShellData } from "@/lib/shell-data";
import { useFailureToast } from "@/lib/useFailureToast";

import styles from "./Usage.module.css";
import { UsagePanel } from "./UsagePanel";

const RADIUS = 7;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

interface UsageControlProps {
  session: SessionRecord | null;
  catalog: BackendCatalog;
  onSession: (session: SessionRecord) => void;
}

// A session without usage data has no ring.
export function UsageControl({ session, catalog, onSession }: UsageControlProps) {
  const { host, token } = useShellData();
  const reportFailure = useFailureToast();
  const [open, setOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const model = usagePanelModel(session, catalog);

  async function refresh() {
    if (!session || refreshing) return;
    setRefreshing(true);
    try {
      onSession(await refreshSessionRateLimitUsage(host, token, session.id));
    } catch (error) {
      reportFailure(error, "failed to refresh rate-limit usage");
    } finally {
      setRefreshing(false);
    }
  }

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
