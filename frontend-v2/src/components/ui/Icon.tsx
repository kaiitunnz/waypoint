import type { ReactNode } from "react";

export type IconName =
  | "chevron-right"
  | "chevron-left"
  | "chevron-down"
  | "chevron-up"
  | "search"
  | "refresh"
  | "reset"
  | "eye"
  | "eye-off"
  | "link"
  | "check"
  | "close"
  | "more"
  | "chat"
  | "terminal"
  | "copy"
  | "panel-right"
  | "panel-left"
  | "arrow-down"
  | "pin"
  | "plus"
  | "arrow-up"
  | "stop"
  | "paperclip"
  | "files"
  | "file"
  | "clock"
  | "slash"
  | "at"
  | "trash"
  | "external"
  | "alert"
  | "info"
  | "sun"
  | "moon"
  | "settings"
  | "sessions"
  | "needs-you"
  | "board"
  | "usage"
  | "tailscale"
  | "ssh";

const PATHS: Record<IconName, ReactNode> = {
  "chevron-right": <path d="M9 6l6 6-6 6" />,
  "chevron-left": <path d="M15 6l-6 6 6 6" />,
  "chevron-down": <path d="M6 9l6 6 6-6" />,
  "chevron-up": <path d="M6 15l6-6 6 6" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M20 20l-4.2-4.2" />
    </>
  ),
  reset: (
    <>
      <path d="M4 9a8 8 0 1 1-.5 5" />
      <path d="M3.5 4v5h5" />
    </>
  ),
  refresh: (
    <>
      <path d="M19 12a7 7 0 1 1-2.1-5" />
      <path d="M19 4.5V9h-4.5" />
    </>
  ),
  eye: (
    <>
      <path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
      <circle cx="12" cy="12" r="2.8" />
    </>
  ),
  "eye-off": (
    <>
      <path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
      <circle cx="12" cy="12" r="2.8" />
      <path d="M4 4l16 16" />
    </>
  ),
  link: (
    <>
      <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
      <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
    </>
  ),
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  chat: (
    <path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-4 3.5V16H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" />
  ),
  terminal: (
    <>
      <rect x="3" y="4.5" width="18" height="15" rx="2" />
      <path d="M7 10l3 2.5L7 15M12.5 15H17" />
    </>
  ),
  copy: (
    <>
      <rect x="8" y="8" width="12" height="12" rx="2" />
      <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
    </>
  ),
  "panel-right": (
    <>
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <path d="M14.5 5v14" />
    </>
  ),
  "panel-left": (
    <>
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <path d="M9.5 5v14" />
    </>
  ),
  "arrow-down": <path d="M12 5v14M6 13l6 6 6-6" />,
  "arrow-up": <path d="M12 19V5M6 11l6-6 6 6" />,
  stop: <rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none" />,
  paperclip: <path d="M20 11.5l-8.2 8.2a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.4 8.4a1.7 1.7 0 0 1-2.4-2.4l7.6-7.6" />,
  files: <path d="M3.5 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />,
  file: (
    <>
      <path d="M13 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-9z" />
      <path d="M13 3.5v6h6" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  slash: <path d="M15 5L9 19" />,
  at: (
    <>
      <circle cx="12" cy="12" r="3.5" />
      <path d="M15.5 8.5v5a2.3 2.3 0 0 0 4.5-.8V12a8 8 0 1 0-3.6 6.7" />
    </>
  ),
  trash: <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12M10.5 11v5M13.5 11v5" />,
  external: <path d="M13 5h6v6M19 5l-8.5 8.5M11 7H6a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-5" />,
  alert: <path d="M12 4l9 16H3zM12 10v4.5M12 17v.01" />,
  info: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 11v5M12 8v.01" />
    </>
  ),
  pin: <path d="M9 4h6l-.8 5.2L17 12.5H7l2.8-3.3L9 4zM12 12.5V20" />,
  more: (
    <>
      <circle cx="5.5" cy="12" r="1.2" />
      <circle cx="12" cy="12" r="1.2" />
      <circle cx="18.5" cy="12" r="1.2" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4" />
    </>
  ),
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />,
  settings: (
    <>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </>
  ),
  sessions: (
    <path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-4 3.5V16H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" />
  ),
  "needs-you": (
    <>
      <path d="M4 13l2.5-7h11L20 13v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z" />
      <path d="M4 13h4.5l1 2h5l1-2H20" />
    </>
  ),
  board: (
    <>
      <rect x="4" y="4.5" width="4.5" height="15" rx="1" />
      <rect x="9.75" y="4.5" width="4.5" height="10" rx="1" />
      <rect x="15.5" y="4.5" width="4.5" height="6.5" rx="1" />
    </>
  ),
  usage: (
    <>
      <path d="M4.5 17a8 8 0 1 1 15 0" />
      <path d="M12 17l3.5-5.5" />
    </>
  ),
  tailscale: (
    <>
      <circle cx="5.5" cy="5.5" r="1.7" />
      <circle cx="12" cy="5.5" r="1.7" />
      <circle cx="18.5" cy="5.5" r="1.7" />
      <circle cx="5.5" cy="12" r="1.7" />
      <circle cx="12" cy="12" r="1.7" />
      <circle cx="18.5" cy="12" r="1.7" />
      <circle cx="12" cy="18.5" r="1.7" />
    </>
  ),
  ssh: (
    <>
      <rect x="3.5" y="5" width="17" height="14" rx="2" />
      <path d="M7.5 10l2.3 2.2-2.3 2.2M12.5 14.5h4" />
    </>
  ),
};

interface IconProps {
  name: IconName;
  size?: number;
  strokeWidth?: number;
}

export function Icon({ name, size = 20, strokeWidth = 1.8 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
