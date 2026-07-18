"use client";

import { useEffect } from "react";

import { registerSessionPresence, releaseSessionPresence } from "@/lib/api";

// Renew cadence for a visible tab. The backend lease is longer (45s), so a
// single missed renewal does not drop presence.
const RENEW_INTERVAL_MS = 15_000;

// Advertise this tab as actively viewing a session while it is visible, so the
// backend suppresses redundant session-interaction notifications for it.
// Presence is a best-effort lease: it starts only after a successful
// authenticated touch, renews every 15s while visible, releases when the tab is
// hidden or unmounted, and the server-side TTL is the authoritative fallback
// when a release is missed. It never relies on beforeunload/sendBeacon, which
// cannot carry the bearer token.
export function useSessionPresence(
  host: string,
  token: string,
  sessionId: string,
) {
  useEffect(() => {
    if (!host || !token || !sessionId) {
      return;
    }
    const viewerId = crypto.randomUUID();
    let renewTimer: ReturnType<typeof setInterval> | null = null;

    const stopRenew = () => {
      if (renewTimer !== null) {
        clearInterval(renewTimer);
        renewTimer = null;
      }
    };

    // Fail open: a transient failure is retried at the next renewal tick.
    const touch = () => {
      void registerSessionPresence(host, token, sessionId, viewerId).catch(
        () => {},
      );
    };

    const release = (keepalive: boolean) => {
      void releaseSessionPresence(host, token, sessionId, viewerId, {
        keepalive,
      }).catch(() => {});
    };

    const activate = () => {
      touch();
      if (renewTimer === null) {
        renewTimer = setInterval(touch, RENEW_INTERVAL_MS);
      }
    };

    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        activate();
      } else {
        stopRenew();
        release(false);
      }
    };

    if (document.visibilityState === "visible") {
      activate();
    }
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      stopRenew();
      release(true);
    };
  }, [host, token, sessionId]);
}
