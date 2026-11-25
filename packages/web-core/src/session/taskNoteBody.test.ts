import { describe, expect, it } from "vitest";

import type { TaskNotificationView } from "../events";
import { ev } from "../test-helpers";
import type { AttachmentPreview } from "../api";
import { taskNoteBody, taskNoteReport, type ReportPreviewState } from "./taskNoteBody";

const view = (extra: Partial<TaskNotificationView> = {}): TaskNotificationView => ({
  id: "n",
  kind: "unknown",
  status: null,
  summary: null,
  event: null,
  note: null,
  taskId: null,
  toolUseId: null,
  resultPreview: null,
  resultTruncated: false,
  eventTruncated: false,
  noteTruncated: false,
  outputAvailable: false,
  outputUnavailableReason: null,
  usage: null,
  ...extra,
});
const spec = (id: string) => ({ id, filename: `${id}.md`, kind: "file", size: 10 });
const noteEvent = (attachments: unknown[] = [], extra: Record<string, unknown> = {}) =>
  ev(1, "system_note", "", { attachments, ...extra });
const ready = (preview: Partial<AttachmentPreview>): ReportPreviewState => ({
  status: "ready",
  preview: { filename: "r", mime: "text/plain", size: 1, binary: false, truncated: false, content: "", content_bytes: 0, ...preview },
});

describe("taskNoteReport", () => {
  it("previews the attachment that is not an inline body", () => {
    const report = taskNoteReport(view(), noteEvent([spec("a"), spec("b")], { inline_attachment_ids: ["a"] }));
    expect(report.reportSpec?.id).toBe("b");
    expect(report.specs).toHaveLength(2);
  });

  it("drops an agent's transcript attachment when its report is inline", () => {
    const report = taskNoteReport(view({ kind: "agent", resultPreview: "text" }), noteEvent([spec("a")]));
    expect(report.specs).toEqual([]);
    expect(report.reportIsInline).toBe(true);
    expect(report.reportSpec).toBeNull();
  });
});

describe("taskNoteBody", () => {
  const plain = taskNoteReport(view(), noteEvent());

  it("has no body for a bare headline", () => {
    const body = taskNoteBody(view({ summary: "Done" }), plain, null);
    expect(body.headline).toBe("Done");
    expect(body.hasBody).toBe(false);
  });

  it("puts the event line under a summary headline", () => {
    const body = taskNoteBody(view({ summary: "Done", event: "details" }), plain, null);
    expect(body.teaser).toBe("details");
    expect(body.hasBody).toBe(true);
    expect(taskNoteBody(view({ event: "only" }), plain, null).teaser).toBeNull();
  });

  it("drops an inline body a loaded report already contains", () => {
    const report = taskNoteReport(view(), noteEvent([spec("a")]));
    const body = taskNoteBody(view({ event: "the  result" }), report, ready({ content: "x the result y" }));
    expect(body.supersedes("the  result")).toBe(true);
    expect(body.supersedes("other")).toBe(false);
    expect(body.supersedes(null)).toBe(false);
  });

  it("links the files when several, or when the preview failed or is partial", () => {
    const one = taskNoteReport(view(), noteEvent([spec("a")]));
    expect(taskNoteBody(view(), one, ready({ content: "x" })).showLinks).toBe(false);
    expect(taskNoteBody(view(), one, ready({ content: "x", truncated: true })).showLinks).toBe(true);
    expect(taskNoteBody(view(), one, { status: "error" }).showLinks).toBe(true);
    const two = taskNoteReport(view(), noteEvent([spec("a"), spec("b")]));
    expect(taskNoteBody(view(), two, null).showLinks).toBe(true);
  });

  it("captions a cut body by whether the rest was kept", () => {
    expect(taskNoteBody(view(), plain, null).cutCaption("x", true)).toBe("not_retained");
    const kept = taskNoteReport(view(), noteEvent([spec("a")]));
    expect(taskNoteBody(view(), kept, null).cutCaption("x", true)).toBe("retained");
    expect(taskNoteBody(view(), kept, null).cutCaption("x", false)).toBeNull();
  });

  it("explains a missing report only when none is attached", () => {
    expect(taskNoteBody(view({ outputAvailable: true }), plain, null).unavailable).toBe("Report unavailable");
    const attached = taskNoteReport(view(), noteEvent([spec("a")]));
    expect(taskNoteBody(view({ outputAvailable: true }), attached, null).unavailable).toBeNull();
  });
});
