"use client";

import { useState } from "react";

import type { TaskNotificationView } from "@waypoint/web-core/events";
import { useReportPreview } from "@waypoint/web-core/hooks/useAttachmentPreview";
import { formatEventTime } from "@waypoint/web-core/session/cardText";
import { TASK_NOTIFICATION_BADGES, taskStatusLamp } from "@waypoint/web-core/session/taskNotificationView";
import { taskNoteBody, taskNoteReport, type ReportPreviewState } from "@waypoint/web-core/session/taskNoteBody";
import type { EventRecord } from "@waypoint/web-core/types";

import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import { useSessionContext } from "../SessionContext";
import { Attachments } from "./Attachments";
import styles from "./TaskNote.module.css";

function ReportPreview({ state }: { state: ReportPreviewState | null }) {
  if (!state || state.status === "loading") return <p className={styles.caption}>Loading report…</p>;
  if (state.status === "error") return <p className={styles.caption}>Report preview unavailable. Open the full file.</p>;
  const { preview } = state;
  if (preview.binary || preview.content === null) return <p className={styles.caption}>Binary report. Open the full file.</p>;
  return (
    <>
      <pre className={styles.result}>{preview.content}</pre>
      {preview.truncated ? <p className={styles.caption}>Preview: first {Math.round(preview.content_bytes / 1024)} KB of the full report</p> : null}
    </>
  );
}

const CAPTIONS = {
  retained: "Preview shown. Full output attached below.",
  not_retained: "Preview. The rest was not retained.",
} as const;

// A task notification (a subagent, a monitor, a background command finishing) as a
// raised card on the route; opening it shows the result and the full report.
export function TaskNote({ view, event }: { view: TaskNotificationView; event: EventRecord }) {
  const [open, setOpen] = useState(false);
  const credentials = useSessionContext();
  const badge = TASK_NOTIFICATION_BADGES[view.kind];
  const lamp = taskStatusLamp(view);
  const report = taskNoteReport(view, event);
  const { specs, reportSpec, inlineReports } = report;
  const previewState = useReportPreview(credentials, reportSpec, open);
  const { headline, teaser, usageParts, unavailable, hasBody, showLinks, supersedes, cutCaption } = taskNoteBody(view, report, previewState);

  const caption = (body: string | null, truncated: boolean) => {
    const kind = cutCaption(body, truncated);
    return kind ? <p className={styles.caption}>{CAPTIONS[kind]}</p> : null;
  };

  const header = (
    <>
      <span className={styles.glyph} aria-hidden="true">
        {badge.glyph}
      </span>
      <span className={styles.headline} title={headline}>
        {headline}
      </span>
      {lamp ? <span className={cx(styles.status, styles[lamp.tone])}>{lamp.label}</span> : null}
      <time className={styles.time}>{formatEventTime(event.ts)}</time>
    </>
  );

  return (
    <div className={styles.note}>
      {hasBody ? (
        <button type="button" className={styles.head} aria-expanded={open} onClick={() => setOpen(!open)}>
          {header}
          <Icon name={open ? "chevron-up" : "chevron-right"} size={14} strokeWidth={2} />
        </button>
      ) : (
        <div className={styles.head}>{header}</div>
      )}
      {teaser && !open ? <p className={styles.teaser}>{teaser}</p> : null}
      {open ? (
        <div className={styles.body}>
          {view.resultPreview && !supersedes(view.resultPreview) ? <pre className={styles.result}>{view.resultPreview}</pre> : null}
          {caption(view.resultPreview, view.resultTruncated)}
          {view.event && !supersedes(view.event) ? <pre className={styles.result}>{view.event}</pre> : null}
          {caption(view.event, view.eventTruncated)}
          {view.note ? <p className={styles.text}>{view.note}</p> : null}
          {caption(view.note, view.noteTruncated)}
          {usageParts.length > 0 ? <p className={styles.caption}>{usageParts.join(" · ")}</p> : null}
          {inlineReports.length > 0 || specs.length > 0 ? (
            <div className={styles.report}>
              <span className={styles.label}>{reportSpec !== null || inlineReports.length > 0 ? "Full report" : "Full output"}</span>
              {inlineReports.map((text, index) =>
                text.trim() ? (
                  <pre className={styles.result} key={index}>
                    {text}
                  </pre>
                ) : (
                  <p className={styles.caption} key={index}>
                    No output
                  </p>
                ),
              )}
              {reportSpec ? <ReportPreview state={previewState} /> : null}
              {showLinks ? <Attachments specs={specs} /> : null}
            </div>
          ) : unavailable ? (
            <p className={styles.caption}>{unavailable}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
