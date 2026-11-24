import { humaniseBackend, displayAgentFor, type BackendCatalog } from "./backends";
import { formatResolvedModelLabel } from "./modelDisplay";
import type { SessionRecord } from "./types";

// One attention vocabulary across the home, the switcher and the rail: lamps
// differ by shape and fill as well as hue.
export type SessionLamp = "needs" | "working" | "done" | "error" | "idle" | "exited";

// A session is unread while its latest activity is newer than the read marker
// (docs/session_read_marker.md). A missing marker reads as read.
export function isUnread(session: SessionRecord): boolean {
  if (!session.read_at) return false;
  return Date.parse(session.last_event_at) > Date.parse(session.read_at);
}

// `hasAsk`: the session has a pending question, approval or plan.
export function sessionLamp(session: SessionRecord, hasAsk: boolean): SessionLamp {
  if (session.status === "exited") return "exited";
  if (hasAsk || session.status === "waiting_input") return "needs";
  if (session.status === "running" || session.status === "starting") return "working";
  if (session.status === "error" || session.status === "interrupted") return "error";
  return isUnread(session) ? "done" : "idle";
}

export function lampWord(lamp: SessionLamp, status: SessionRecord["status"]): string {
  switch (lamp) {
    case "needs":
      return "Needs you";
    case "working":
      return "Working";
    case "done":
      return "Done";
    case "error":
      return status === "interrupted" ? "Interrupted" : "Error";
    case "idle":
      return "Idle";
    case "exited":
      return "Exited";
  }
}

const CLAUDE_ALIAS = /^(opus|sonnet|haiku|fable)(?:\[[^\]]*\])?$/;

// A short model name for a row: "Opus 4.8", "gpt-6.2-sol", "Sonnet".
export function shortModelLabel(session: SessionRecord): string | null {
  const resolved = formatResolvedModelLabel(session.resolved_model, null, []);
  if (resolved) return resolved;
  const selection = session.model;
  if (!selection) return null;
  const alias = CLAUDE_ALIAS.exec(selection);
  if (alias) return alias[1].charAt(0).toUpperCase() + alias[1].slice(1);
  return selection.replace(/\[[^\]]*\]$/, "").split("/").pop() ?? selection;
}

// "Claude Code, Opus 4.8".
export function agentModelLabel(session: SessionRecord, catalog?: BackendCatalog): string {
  const agent = humaniseBackend(displayAgentFor(session.backend, session.transport, catalog), catalog);
  const model = shortModelLabel(session);
  return model ? `${agent}, ${model}` : agent;
}

const MINUTE = 60_000;

const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

const WEEKDAY = new Intl.DateTimeFormat("en", { weekday: "short" });
const MONTH_DAY = new Intl.DateTimeFormat("en", { month: "short", day: "numeric" });
const MONTH_DAY_YEAR = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric" });

// A row's time: "now", "2m", "08:40", "Yesterday", "Mon", "Nov 12".
export function formatSessionTime(iso: string, now: number = Date.now()): string {
  const at = new Date(iso);
  const ms = at.getTime();
  if (!Number.isFinite(ms)) return "";
  const nowDate = new Date(now);
  const elapsed = now - ms;
  if (elapsed < MINUTE) return "now";
  if (elapsed < 60 * MINUTE) return `${Math.floor(elapsed / MINUTE)}m`;
  if (sameDay(at, nowDate)) {
    return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
  }
  const startOfToday = new Date(nowDate.getFullYear(), nowDate.getMonth(), nowDate.getDate()).getTime();
  const startOfThatDay = new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime();
  const days = Math.round((startOfToday - startOfThatDay) / (24 * 60 * MINUTE));
  if (days === 1) return "Yesterday";
  if (days > 1 && days < 7) return WEEKDAY.format(at);
  return (at.getFullYear() === nowDate.getFullYear() ? MONTH_DAY : MONTH_DAY_YEAR).format(at);
}
