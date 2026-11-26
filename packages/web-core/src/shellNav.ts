export const sessionHref = (id: string) => `/session/${encodeURIComponent(id)}`;

// The session's workspace explorer, opened at `path` when given.
export const sessionFilesHref = (id: string, path?: string) =>
  `${sessionHref(id)}/files${path === undefined ? "" : `?path=${encodeURIComponent(path)}`}`;

export type NavKey = "sessions" | "needs-you" | "assistant" | "board" | "usage";

export interface NavDestination {
  key: NavKey;
  label: string;
  href: string;
}

// Top-level destinations, in tab and rail order.
export const NAV_DESTINATIONS: NavDestination[] = [
  { key: "sessions", label: "Sessions", href: "/" },
  { key: "needs-you", label: "Needs you", href: "/inbox" },
  { key: "assistant", label: "Assistant", href: "/assistant" },
  { key: "board", label: "Board", href: "/board" },
  { key: "usage", label: "Usage", href: "/telemetry" },
];

function normalize(pathname: string): string {
  const trimmed = pathname.replace(/\/+$/, "");
  return trimmed === "" ? "/" : trimmed;
}

const isUnder = (path: string, root: string) => path === root || path.startsWith(`${root}/`);

// The destination a path belongs to, or null for a path outside the navigation.
export function activeNavKey(pathname: string): NavKey | null {
  const path = normalize(pathname);
  if (path === "/" || isUnder(path, "/session")) return "sessions";
  if (isUnder(path, "/inbox")) return "needs-you";
  if (isUnder(path, "/assistant")) return "assistant";
  if (isUnder(path, "/board")) return "board";
  if (isUnder(path, "/telemetry")) return "usage";
  return null;
}

// A page outside the navigation, one path per section.
export const SETTINGS_HREF = "/settings";
export const settingsSectionHref = (id: string) => `${SETTINGS_HREF}/${encodeURIComponent(id)}`;
export const isSettingsPath = (pathname: string) => isUnder(normalize(pathname), SETTINGS_HREF);

// The section a settings path names, if any.
export function settingsSectionOf(pathname: string): string | undefined {
  const path = normalize(pathname);
  return path.startsWith(`${SETTINGS_HREF}/`) ? decodeURIComponent(path.slice(SETTINGS_HREF.length + 1)) : undefined;
}

// The touch tab bar shows on the top-level screens only. A session, a single
// needs-you item and the assistant take the whole screen.
export function showsTabBar(pathname: string): boolean {
  return ["/", "/inbox", "/board", "/telemetry"].includes(normalize(pathname));
}

const MAX_BADGE_COUNT = 99;

// The badge text: absent at zero, capped at 99+.
export function formatBadgeCount(count: number): string | null {
  if (count <= 0) return null;
  return count > MAX_BADGE_COUNT ? `${MAX_BADGE_COUNT}+` : String(count);
}
