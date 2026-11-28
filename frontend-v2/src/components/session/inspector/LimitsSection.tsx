"use client";

import { useState } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { useServerNow } from "@waypoint/web-core/hooks/useServerNow";
import { usageBars, usagePanelModel } from "@waypoint/web-core/session/usagePanel";
import type { SessionRecord } from "@waypoint/web-core/types";

import { UsagePanel } from "@/components/composer/UsagePanel";
import { useUsageRefresh } from "@/components/composer/useUsageRefresh";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Meter } from "@/components/ui/Meter";
import { cx } from "@/lib/cx";

import { Section } from "./Section";
import styles from "./LimitsSection.module.css";

interface LimitsSectionProps {
  session: SessionRecord;
  catalog: BackendCatalog;
  onSession: (session: SessionRecord) => void;
}

// The context window and each rate limit as a bar; the full usage opens from the heading.
export function LimitsSection({ session, catalog, onSession }: LimitsSectionProps) {
  const now = useServerNow(60_000);
  const [open, setOpen] = useState(false);
  const { refresh, refreshing } = useUsageRefresh(session, onSession);
  const model = usagePanelModel(session, catalog);
  const bars = usageBars(model, now);
  if (bars.length === 0) return null;

  return (
    <>
      <Section
        title="Context and limits"
        action={
          <Button variant="text" size="sm" onClick={() => setOpen(true)} aria-haspopup="dialog">
            Usage
          </Button>
        }
      >
        <ul className={styles.bars}>
          {bars.map((bar) => (
            <li key={bar.id} className={styles.bar}>
              <span className={styles.label}>{bar.label}</span>
              <span className={styles.value}>
                <b className={cx(bar.tone !== "good" && styles[bar.tone])}>{bar.percent !== null ? `${bar.percent}%` : "—"}</b>
                {bar.detail ? ` ${bar.detail}` : null}
              </span>
              <Meter
                value={bar.percent}
                tone={bar.tone === "good" ? (bar.id === "context" ? "working" : "calm") : bar.tone}
                label={bar.label}
                className={styles.meter}
              />
            </li>
          ))}
        </ul>
      </Section>
      <Dialog open={open} onClose={() => setOpen(false)} title="Usage">
        <UsagePanel model={model} catalog={catalog} onRefresh={() => void refresh()} refreshing={refreshing} />
      </Dialog>
    </>
  );
}
