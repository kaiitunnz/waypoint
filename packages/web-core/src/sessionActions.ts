import type { SessionRecord } from "./types";

export function withTitle(session: SessionRecord, title: string): SessionRecord {
  return { ...session, title };
}

// Undoes an optimistic rename, unless a newer rename or a server update has
// superseded it since. Only the title goes back: the rest of the record may have
// moved on while the request was in flight.
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

export function isExited(session: SessionRecord): boolean {
  return session.status === "exited";
}
