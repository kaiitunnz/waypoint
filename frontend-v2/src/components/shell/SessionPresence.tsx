"use client";

import { useSessionPresence } from "@waypoint/web-core/hooks/useSessionPresence";

import { useShellData } from "@/lib/shell-data";

// Holds presence on a session while its page is open and visible, which marks the
// session read on every device (docs/session_read_marker.md).
export function SessionPresence({ id }: { id: string }) {
  const { host, token } = useShellData();
  useSessionPresence(host, token, id);
  return null;
}
