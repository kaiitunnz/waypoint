"use client";

import { useSessionPresence } from "@waypoint/web-core/hooks/useSessionPresence";

import { useShellData } from "@/lib/shell-data";

// Presence moves the session's read marker (docs/session_read_marker.md).
export function SessionPresence({ id }: { id: string }) {
  const { host, token } = useShellData();
  useSessionPresence(host, token, id);
  return null;
}
