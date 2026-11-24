"use client";

import { useEffect } from "react";

import { installFileDropGuard } from "@waypoint/web-core/fileDrop";

// Cancels file drops nobody accepted, which the browser would open in the tab.
export function FileDropGuard() {
  useEffect(() => installFileDropGuard(), []);
  return null;
}
