"use client";

import { useState } from "react";

import { humaniseBackend, type BackendCatalog } from "@waypoint/web-core/backends";
import { coverageLabel, type UsagePanelModel } from "@waypoint/web-core/session/usagePanel";
import {
  formatRateLimitWindowReset,
  formatRateLimitWindowTokens,
  formatRelativeTime,
  formatTokens,
  rateLimitWindowPercent,
  usageTone,
  type UsageTone,
} from "@waypoint/web-core/usage";

import { ActionChip } from "@/components/ui/ActionChip";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { Meter, type MeterTone } from "@/components/ui/Meter";
import { cx } from "@/lib/cx";

import styles from "./Usage.module.css";

function meterTone(tone: UsageTone, calm: MeterTone): MeterTone {
  return tone === "good" ? calm : tone;
}

function Chips({ items }: { items: [label: string, tokens: number][] }) {
  return (
    <ul className={styles.chips}>
      {items.map(([label, tokens]) => (
        <li key={label}>
          <span>{label}</span>
          <strong>{formatTokens(tokens)}</strong>
        </li>
      ))}
    </ul>
  );
}

interface UsagePanelProps {
  model: UsagePanelModel;
  catalog: BackendCatalog;
  onRefresh: () => void;
  refreshing: boolean;
}

export function UsagePanel({ model, catalog, onRefresh, refreshing }: UsagePanelProps) {
  const [explained, setExplained] = useState(false);
  const { contextUsage, tokenUsage, rateLimitUsage } = model;

  return (
    <div className={styles.panel}>
      {rateLimitUsage ? (
        <section className={styles.block}>
          <header className={styles.head}>
            <h3>Rate limits</h3>
            <ActionChip icon="refresh" onClick={onRefresh} aria-disabled={refreshing}>
              {refreshing ? "Refreshing" : "Refresh"}
            </ActionChip>
          </header>
          {rateLimitUsage.windows.length > 0 ? (
            <ul className={styles.windows}>
              {rateLimitUsage.windows.map((window) => {
                const percent = rateLimitWindowPercent(window);
                const reset = formatRateLimitWindowReset(window);
                const tokens = formatRateLimitWindowTokens(window);
                const tone = usageTone(percent);
                return (
                  <li key={window.id}>
                    <p className={styles.line}>
                      <span>{window.label}</span>
                      <span className={styles.reset}>
                        {reset ? (
                          <>
                            <Icon name="clock" size={13} />
                            {reset}
                          </>
                        ) : null}
                      </span>
                      <strong className={cx(styles.percent, styles[tone])}>{percent !== null ? `${percent}%` : "—"}</strong>
                    </p>
                    <Meter value={percent ?? 0} tone={meterTone(tone, "calm")} label={window.label} className={styles.meter} />
                    {tokens ? <p className={styles.muted}>{tokens}</p> : null}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className={styles.muted}>No quota tracked on this plan.</p>
          )}
          <dl className={styles.meta}>
            <div>
              <dt>Updated</dt>
              <dd title={new Date(rateLimitUsage.updated_at).toLocaleString()}>
                {formatRelativeTime(rateLimitUsage.updated_at)}
              </dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>{model.rateLimitSourceLabel}</dd>
            </div>
            {rateLimitUsage.credits_remaining !== null && rateLimitUsage.credits_remaining !== undefined ? (
              <div>
                <dt>Credits</dt>
                <dd>
                  {`${rateLimitUsage.credits_currency ?? "credits"} ${rateLimitUsage.credits_remaining.toFixed(2)}`}
                </dd>
              </div>
            ) : null}
          </dl>
        </section>
      ) : null}

      {contextUsage ? (
        <section className={styles.block}>
          <header className={styles.head}>
            <h3>Current context window</h3>
            <span className={styles.muted}>{humaniseBackend(contextUsage.source, catalog)}</span>
          </header>
          <p className={styles.line}>
            <span>
              {model.contextUnreported
                ? `${model.contextWindowTokens !== null ? formatTokens(model.contextWindowTokens) : "—"} token window`
                : `${formatTokens(contextUsage.used_tokens)} of ${model.contextWindowTokens !== null ? formatTokens(model.contextWindowTokens) : "—"} tokens`}
            </span>
            <strong className={cx(styles.percent, styles[model.contextTone])}>
              {model.contextPercent !== null ? `${model.contextPercent}%` : "—"}
            </strong>
          </p>
          {model.contextUnreported ? (
            <p className={styles.muted}>This endpoint doesn’t report context usage</p>
          ) : (
            <Meter
              value={model.contextPercent ?? 0}
              tone={meterTone(model.contextTone, "working")}
              label="Context window"
              className={styles.meter}
            />
          )}
          <p className={styles.muted} title={new Date(contextUsage.updated_at).toLocaleString()}>
            Updated {formatRelativeTime(contextUsage.updated_at)}
          </p>
          {model.contextBreakdown.length > 0 ? (
            <>
              <p className={styles.muted}>Last turn</p>
              <Chips items={model.contextBreakdown} />
            </>
          ) : null}
        </section>
      ) : null}

      {tokenUsage ? (
        <section className={styles.block}>
          <header className={styles.head}>
            <h3>Tracked session total</h3>
            <span className={styles.muted}>{humaniseBackend(tokenUsage.source, catalog)}</span>
          </header>
          {model.hasTrackedTurns ? (
            <>
              <p className={styles.line}>
                <span>
                  {tokenUsage.tracked_turns} {tokenUsage.tracked_turns === 1 ? "turn" : "turns"}
                </span>
                {typeof tokenUsage.display_total_tokens === "number" ? (
                  <span className={styles.total}>
                    <strong>{formatTokens(tokenUsage.display_total_tokens)}</strong> cumulative tokens
                    <IconButton
                      label="About cumulative tokens"
                      size="sm"
                      aria-expanded={explained}
                      onClick={() => setExplained((value) => !value)}
                    >
                      <Icon name="info" size={15} />
                    </IconButton>
                  </span>
                ) : null}
              </p>
              {explained ? <p className={styles.muted}>Counts the whole conversation, re-read every turn.</p> : null}
              {model.hasTokenTotals ? <Chips items={model.tokenTotals} /> : null}
            </>
          ) : null}
          <p className={cx(styles.muted, tokenUsage.coverage !== "entire_waypoint_session" && styles.partial)}>
            {coverageLabel(tokenUsage)}
          </p>
        </section>
      ) : null}
    </div>
  );
}
