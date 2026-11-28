import type { ExistingEnvEntry, NewEnvEntry } from "../hooks/useSessionSettings";
import { envRowState, type EnvEntry } from "../launch/env";

// The environment a session's settings edit, as the rows of the launch form's editor.
// A stored value is never fetched, so an existing key's value is empty: a row is kept,
// replaced by what is typed (`original` is then set), or removed. Keys the runtime or
// the staged profile owns are left out of the rows and left as they are.

// Existing rows take their place in the whole list as an id, so a staged profile that
// hides a key does not renumber the rest; an added row keeps its own id, which the editor
// makes unique among the rows it shows.
export function settingsEnvEntries(existing: readonly ExistingEnvEntry[], added: readonly NewEnvEntry[], hiddenKeys: readonly string[] = []): EnvEntry[] {
  const hidden = new Set(hiddenKeys);
  const rows: EnvEntry[] = [];
  existing.forEach((entry, index) => {
    if (hidden.has(entry.key)) return;
    const id = index + 1;
    if (entry.op === "remove") rows.push({ id, key: entry.key, value: "", removed: true });
    else if (entry.op === "replace") rows.push({ id, key: entry.key, value: entry.value, original: "" });
    else rows.push({ id, key: entry.key, value: "" });
  });
  for (const entry of added) rows.push({ id: entry.id, key: entry.key, value: entry.value, added: true });
  return rows;
}

// The editor's rows back as the settings' own lists; a key the rows leave out is kept as it was.
export function applyEnvEntries(entries: readonly EnvEntry[], existing: readonly ExistingEnvEntry[]): { existing: ExistingEnvEntry[]; added: NewEnvEntry[] } {
  const nextExisting = existing.map((entry, index): ExistingEnvEntry => {
    const row = entries.find((candidate) => !candidate.added && candidate.id === index + 1);
    if (!row) return entry;
    switch (envRowState(row)) {
      case "remove":
        return { key: entry.key, op: "remove", value: "" };
      case "edit":
        return { key: entry.key, op: "replace", value: row.value };
      default:
        return { key: entry.key, op: "keep", value: "" };
    }
  });
  const added = entries.filter((row) => row.added).map((row): NewEnvEntry => ({ id: row.id, key: row.key, value: row.value }));
  return { existing: nextExisting, added };
}
