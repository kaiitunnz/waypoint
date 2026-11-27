"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import { serverClockOffset, subscribeServerClock } from "../serverClock";

// The server's time, ticking every `intervalMs`, for labels that compare a server
// timestamp with now. A new reading of the clock offset shows at once.
export function useServerNow(intervalMs = 60_000): number {
  const offset = useSyncExternalStore(subscribeServerClock, serverClockOffset, () => 0);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now + offset;
}
