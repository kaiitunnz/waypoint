"use client";

import { useEffect, useState } from "react";

// The current time in ms, refreshed every `intervalMs`, for labels like "2m".
export function useNow(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}
