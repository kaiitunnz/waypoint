"use client";

import { useTheme } from "@/lib/theme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const label = theme === "light" ? "Switch to dark theme" : "Switch to light theme";
  const icon = theme === "light" ? "☽" : "☀";

  return (
    <button
      className="theme-toggle"
      onClick={toggle}
      aria-label={label}
      title={label}
    >
      {icon}
    </button>
  );
}
