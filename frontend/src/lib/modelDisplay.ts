/**
 * Display helpers for model/effort values shared across the launch panel,
 * the effort picker, and the session header badge. Single-sourced so the
 * label maps can't drift between call sites.
 */

import type { BackendModelOption } from "@/lib/types";

const EFFORT_LABELS: Record<string, string> = {
  none: "None",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

export function effortLabel(value: string): string {
  return EFFORT_LABELS[value] ?? value;
}

// Family ids as they appear in a concrete claude-<family>-<version> id,
// title-cased for display (e.g. "opus" -> "Opus").
const CLAUDE_MODEL_FAMILY_LABELS: Record<string, string> = {
  opus: "Opus",
  sonnet: "Sonnet",
  haiku: "Haiku",
  fable: "Fable",
};

// Best-effort "claude-<family>-<version segments>" -> "<Family> <version>"
// (e.g. "claude-opus-4-8" -> "Opus 4.8", "claude-sonnet-5" -> "Sonnet 5").
// Returns null when the id doesn't look like a claude concrete id.
function parseClaudeConcreteModelId(id: string): string | null {
  if (!id.startsWith("claude-")) {
    return null;
  }
  const segments = id.slice("claude-".length).split("-");
  const family = segments[0];
  const familyLabel = CLAUDE_MODEL_FAMILY_LABELS[family];
  if (!familyLabel || segments.length < 2) {
    return null;
  }
  const version = segments.slice(1).join(".");
  return `${familyLabel} ${version}`;
}

/**
 * Label for a session's resolved (concrete) model, for display only.
 *
 * `resolvedModel` is the concrete id the backend actually ran (e.g.
 * "claude-opus-4-8"); `selectionId` is the user's selection (e.g.
 * "opus[1m]"), used only to reattach the context-window variant the
 * resolved id itself doesn't carry. Returns null when there's no resolved
 * model to show — callers should fall back to their existing
 * selection-label logic.
 */
export function formatResolvedModelLabel(
  resolvedModel: string | null | undefined,
  selectionId: string | null | undefined,
  modelOptions: BackendModelOption[],
): string | null {
  if (!resolvedModel) {
    return null;
  }
  const catalogLabel = modelOptions.find((opt) => opt.id === resolvedModel)?.label;
  const base = catalogLabel ?? parseClaudeConcreteModelId(resolvedModel) ?? resolvedModel;
  return selectionId?.endsWith("[1m]") ? `${base} (1M context)` : base;
}
