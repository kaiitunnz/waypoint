"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchNLInsight } from "@/lib/api";

// Bottom-center glass floater linking to the full /telemetry dashboard —
// mirrors InboxDock/assistant-fab's treatment but sits center so it never
// collides with either corner dock. Optionally shows a small dot when an
// NL-insight digest is available (PR-NL, opt-in and off by default — a 404/409
// from a not-yet-deployed or disabled feature is swallowed silently, same as
// InboxDock degrades on a briefly-down API).
export function TelemetryDock({ host, token }: { host: string; token: string }) {
  const [nlAvailable, setNlAvailable] = useState(false);

  useEffect(() => {
    if (!host || !token) {
      setNlAvailable(false);
      return;
    }
    let active = true;
    fetchNLInsight(host, token)
      .then((response) => {
        if (active) setNlAvailable(Boolean(response?.available && response.insight));
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [host, token]);

  return (
    <Link className="telemetry-dock" href="/telemetry" aria-label="Open telemetry">
      <span className="telemetry-dock-glyph" aria-hidden="true">
        <GaugeIcon />
      </span>
      <span className="telemetry-dock-label">Telemetry</span>
      {nlAvailable ? (
        <span className="telemetry-dock-badge" role="status">
          <span className="sr-only">AI insight available</span>
        </span>
      ) : null}
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
