import { modelLabelFor } from "../modelDisplay";
import type { BackendModelOption, EventRecord } from "../types";

// 24-hour HH:MM in the viewer's timezone.
export function formatEventTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

// A reasoning disclosure's one-line preview: plain text, since summaries often
// open with **bold** headings.
export function reasoningPreview(text: string, max = 140): string {
  const collapsed = text
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/\s+/g, " ")
    .trim();
  if (collapsed.length <= max) return collapsed;
  return `${collapsed.slice(0, max - 1).trimEnd()}…`;
}

// A blank agent message (a whitespace-only OpenCode text part) carries nothing
// to render.
export function isBlankAgentMessage(event: EventRecord): boolean {
  return event.kind === "agent_output" && !event.text.trim();
}

export function sendUserFileInput(event: EventRecord): {
  files: string[];
  caption: string | null;
} {
  const input = (event.metadata?.payload as { input?: unknown } | undefined)?.input as
    | { files?: unknown; caption?: unknown }
    | undefined;
  const files = Array.isArray(input?.files)
    ? input.files.filter((p): p is string => typeof p === "string" && p.length > 0)
    : [];
  const caption =
    typeof input?.caption === "string" && input.caption.trim() ? input.caption : null;
  return { files, caption };
}

// "Model changed from Opus 4.8 to Sonnet 5.5" or "Switched to Sonnet 5.5".
export function modelChangeText(event: EventRecord, options: BackendModelOption[] = []): string {
  const current =
    typeof event.metadata?.current_model === "string" ? event.metadata.current_model : "";
  const previous =
    typeof event.metadata?.previous_model === "string" ? event.metadata.previous_model : "";
  const currentLabel = modelLabelFor(current, options);
  return previous
    ? `Model changed from ${modelLabelFor(previous, options)} to ${currentLabel}`
    : `Switched to ${currentLabel}`;
}
