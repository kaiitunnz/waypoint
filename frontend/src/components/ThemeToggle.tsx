"use client";

import { useTheme } from "@/lib/theme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const label = theme === "light" ? "Switch to dark theme" : "Switch to light theme";
  const icon = theme === "light" ? "☽" : "☀";

  // The theme is only known on the client (the server always renders the
  // "dark" default), so the icon/label legitimately differ between the
  // server and the first client render. Tell React the divergence is
  // intentional instead of tripping a hydration mismatch.
  return (
    <button
      className="theme-toggle"
      onClick={toggle}
      aria-label={label}
      title={label}
      suppressHydrationWarning
    >
      <span aria-hidden="true" suppressHydrationWarning>
        {icon}
      </span>
    </button>
  );
}
