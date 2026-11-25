import type { AttachmentPreview } from "../api";
import { attachmentSpecsFor } from "../attachments";
import { capturedTexts, inlineAttachmentIds, type TaskNotificationView } from "../events";
import type { AttachmentSpec, EventRecord } from "../types";
import { collapseWhitespace } from "./toolPresentation";
import { reportUnavailableText, taskNoteHeadline, taskUsageParts } from "./taskNotificationView";

export type ReportPreviewState =
  | { status: "loading" }
  | { status: "ready"; preview: AttachmentPreview }
  | { status: "error" };

// The report a task note carries, independent of whether its preview has loaded.
export interface TaskNoteReport {
  specs: AttachmentSpec[];
  // The attachment worth previewing: a separately captured report, else a spilled
  // body holding the text the inline prefix was cut from.
  reportSpec: AttachmentSpec | null;
  // Small enough that the runtime read it into the event; no request needed.
  inlineReports: string[];
  reportIsInline: boolean;
}

export function taskNoteReport(view: TaskNotificationView, event: EventRecord): TaskNoteReport {
  const inlineIds = inlineAttachmentIds(event);
  // An Agent's captured output-file is its transcript, not its report. Older
  // events still carry one; it is dropped, but spilled bodies stay.
  const reportIsInline = view.kind === "agent" && Boolean(view.resultPreview);
  const specs = attachmentSpecsFor(event).filter((spec) => !reportIsInline || inlineIds.has(spec.id));
  const reportSpec = specs.find((spec) => !inlineIds.has(spec.id)) ?? specs[0] ?? null;
  return { specs, reportSpec, inlineReports: capturedTexts(event), reportIsInline };
}

export type CutCaption = "retained" | "not_retained";

export interface TaskNoteBody {
  headline: string;
  // The `event` line riding under the headline when a summary is the headline.
  teaser: string | null;
  usageParts: string[];
  unavailable: string | null;
  hasBody: boolean;
  // The attachments are linked: several always, a lone one once its preview
  // failed or came back partial.
  showLinks: boolean;
  // Whether a loaded report already contains `body`, making it a duplicate.
  supersedes: (body: string | null) => boolean;
  // The note under an inline block that was cut, unless a loaded report holds the rest.
  cutCaption: (body: string | null, truncated: boolean) => CutCaption | null;
}

export function taskNoteBody(
  view: TaskNotificationView,
  report: TaskNoteReport,
  previewState: ReportPreviewState | null,
): TaskNoteBody {
  const { specs, inlineReports, reportIsInline, reportSpec } = report;
  const previewText =
    previewState?.status === "ready" && !previewState.preview.binary
      ? previewState.preview.content
      : (inlineReports[0] ?? null);
  // A captured report is often the fuller version of a body the event carries
  // inline, so when it contains that body the duplicate is dropped.
  const supersedes = (body: string | null) =>
    Boolean(body && previewText && collapseWhitespace(previewText).includes(collapseWhitespace(body)));
  const previewIsPartial =
    previewState?.status === "error" ||
    (previewState?.status === "ready" &&
      (previewState.preview.truncated || previewState.preview.binary || previewState.preview.content === null));
  const usageParts = taskUsageParts(view.usage);
  // An ignored transcript leaves nothing missing, but those events were stored
  // with output_available set.
  const unavailable = reportSpec !== null || reportIsInline ? null : reportUnavailableText(view);
  // Card-wide, never the failure flag, so one failed spill cannot disown a
  // sibling attached below.
  const retained = specs.length > 0 || inlineReports.length > 0;
  return {
    headline: taskNoteHeadline(view),
    teaser: view.summary ? view.event : null,
    usageParts,
    unavailable,
    hasBody: Boolean(
      view.resultPreview ||
        view.event ||
        view.note ||
        usageParts.length > 0 ||
        specs.length > 0 ||
        inlineReports.length > 0 ||
        unavailable,
    ),
    showLinks: specs.length > 1 || previewIsPartial,
    supersedes,
    cutCaption: (body, truncated) => (truncated && !supersedes(body) ? (retained ? "retained" : "not_retained") : null),
  };
}
