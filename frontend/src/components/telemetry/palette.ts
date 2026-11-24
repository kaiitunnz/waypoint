import type { UnifiedTokenCategory } from "@waypoint/web-core/tokens";
import type { InstanceStorageCategory } from "@waypoint/web-core/types";

// Chart colours resolve to the categorical `--tm-series-*` slots and the muted
// `--tm-token-reread` band defined in globals.css.
const TOKEN_CATEGORY_COLORS: Record<UnifiedTokenCategory, string> = {
  fresh_input: "var(--tm-series-1)",
  output: "var(--tm-series-2)",
  reasoning: "var(--tm-series-3)",
  cache_write: "var(--tm-series-5)",
  cache_read: "var(--tm-token-reread)",
};

export function tokenCategoryColor(category: string): string {
  return TOKEN_CATEGORY_COLORS[category as UnifiedTokenCategory] ?? "var(--tm-series-6)";
}

const INSTANCE_CATEGORY_COLORS: Record<InstanceStorageCategory, string> = {
  database: "var(--tm-series-1)",
  sqlite_companions: "var(--tm-series-2)",
  live_sessions: "var(--tm-series-3)",
  orphan_sessions: "var(--tm-series-4)",
  attachments: "var(--tm-series-5)",
  unclassified: "var(--tm-series-6)",
};

export function instanceCategoryColor(category: string): string {
  return INSTANCE_CATEGORY_COLORS[category as InstanceStorageCategory] ?? "var(--tm-series-6)";
}
