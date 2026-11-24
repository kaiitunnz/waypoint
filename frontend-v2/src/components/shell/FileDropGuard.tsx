"use client";

import { useEffect } from "react";

import { installFileDropGuard } from "@waypoint/web-core/fileDrop";

export function FileDropGuard() {
  useEffect(() => installFileDropGuard(), []);
  return null;
}
