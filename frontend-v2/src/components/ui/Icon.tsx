import type { ReactNode } from "react";

export type IconName =
  | "chevron-right"
  | "chevron-left"
  | "chevron-down"
  | "chevron-up"
  | "search"
  | "refresh"
  | "eye"
  | "eye-off"
  | "link"
  | "check"
  | "close"
  | "more"
  | "plus"
  | "sun"
  | "moon"
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
