// Environment-variable rows of a launch form. A row may carry staged edits: the
// value it had before an edit (`original`), a removal that applies on launch
// (`removed`), or that it was added in this form (`added`). Callers that edit rows
// in place leave those unset.
export interface EnvEntry {
  id: number;
  key: string;
  value: string;
  original?: string;
  removed?: boolean;
  added?: boolean;
}

// Ids are stable and increasing, so keys survive edits and removals.
export function recordToEntries(record: Record<string, string> | undefined): EnvEntry[] {
  return Object.entries(record ?? {}).map(([key, value], index) => ({ id: index + 1, key, value }));
}

// Keys are trimmed, blank keys and removed rows dropped, and the last value wins on
// a duplicate key.
export function entriesToRecord(entries: EnvEntry[]): Record<string, string> {
  const record: Record<string, string> = {};
  for (const entry of entries) {
    const key = entry.key.trim();
    if (!key || entry.removed) continue;
    record[key] = entry.value;
  }
  return record;
}

function nextEnvId(entries: EnvEntry[]): number {
  return entries.reduce((max, entry) => Math.max(max, entry.id), 0) + 1;
}

export function addEnvEntry(entries: EnvEntry[]): EnvEntry[] {
  return [...entries, { id: nextEnvId(entries), key: "", value: "", added: true }];
}

export function updateEnvEntry(entries: EnvEntry[], id: number, patch: Partial<Pick<EnvEntry, "key" | "value">>): EnvEntry[] {
  return entries.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry));
}

export function deleteEnvEntry(entries: EnvEntry[], id: number): EnvEntry[] {
  return entries.filter((entry) => entry.id !== id);
}

// Starts replacing a row's value, remembering the one it replaces.
export function editEnvValue(entries: EnvEntry[], id: number, value: string): EnvEntry[] {
  return entries.map((entry) => {
    if (entry.id !== id) return entry;
    const original = entry.added || entry.original !== undefined ? entry.original : entry.value;
    return { ...entry, value, original };
  });
}

// Marks a row for removal on launch.
export function stageEnvRemoval(entries: EnvEntry[], id: number): EnvEntry[] {
  return entries.flatMap((entry) => {
    if (entry.id !== id) return [entry];
    // A row added here has nothing to strike.
    if (entry.added) return [];
    return [{ ...entry, value: entry.original ?? entry.value, original: undefined, removed: true }];
  });
}

// Puts a row back as it was: an added row goes, an edit or a removal is undone.
export function undoEnvEntry(entries: EnvEntry[], id: number): EnvEntry[] {
  return entries.flatMap((entry) => {
    if (entry.id !== id) return [entry];
    if (entry.added) return [];
    return [{ id: entry.id, key: entry.key, value: entry.original ?? entry.value }];
  });
}

export type EnvRowState = "keep" | "edit" | "remove" | "add";

export function envRowState(entry: EnvEntry): EnvRowState {
  if (entry.removed) return "remove";
  if (entry.added) return "add";
  return entry.original !== undefined ? "edit" : "keep";
}

const SECRET_KEY = /TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|AUTH|CREDENTIAL|PRIVATE|_KEY$|^KEY$/i;
const MASK = "••••••••";
const SHORT_LIMIT = 28;

// What a row shows for its value: secret-looking keys are masked, keeping a short
// prefix such as `ghp_`; other values are cut to a short form.
export function envValueDisplay(key: string, value: string): string {
  if (!value) return "";
  if (SECRET_KEY.test(key)) {
    const prefix = /^[A-Za-z]{2,5}[_-]/.exec(value)?.[0] ?? "";
    return `${prefix}${MASK}`;
  }
  return value.length > SHORT_LIMIT ? `${value.slice(0, SHORT_LIMIT - 1)}…` : value;
}

// A one-line summary of the variables a form sets, for a collapsed row.
export function envSummary(entries: EnvEntry[]): string {
  return entries
    .filter((entry) => !entry.removed && entry.key.trim())
    .map((entry) => entry.key.trim())
    .join(", ");
}
