import type { SessionRecord } from "./types";

// Undoes an optimistic rename unless a newer rename or server update superseded
// it; only the title reverts.
export function revertTitle(
  session: SessionRecord,
  optimisticTitle: string,
  previousTitle: string,
): SessionRecord {
  return session.title === optimisticTitle ? { ...session, title: previousTitle } : session;
}

// The trimmed title to send, or null when renaming would change nothing.
export function renameTarget(session: SessionRecord, draft: string): string | null {
  const title = draft.trim();
  return title && title !== session.title ? title : null;
}
