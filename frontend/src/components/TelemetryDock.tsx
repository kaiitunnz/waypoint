"use client";

import Link from "next/link";

// Bottom-center glass floater linking to the full /telemetry dashboard —
// mirrors InboxDock/assistant-fab's treatment but sits center so it never
// collides with either corner dock.
export function TelemetryDock() {
  return (
    <Link className="telemetry-dock" href="/telemetry" aria-label="Open telemetry">
      <span className="telemetry-dock-glyph" aria-hidden="true">
        <GaugeIcon />
      </span>
      <span className="telemetry-dock-label">Telemetry</span>
    </Link>
  );
}

function GaugeIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 15a8 8 0 1 1 16 0" />
      <path d="M12 15V9" />
      <path d="M12 15 16 11" />
    </svg>
  );
}
