"use client";

import { useSyncExternalStore } from "react";

import { STORAGE_KEY, THEME_COLORS, type Theme } from "./theme-script";

export type { Theme };

const listeners = new Set<() => void>();

function readStored(): Theme | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // localStorage may throw in private mode; the choice then lasts for the page.
  }
  return null;
}

function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "theme-color";
    document.head.appendChild(meta);
  }
  meta.content = THEME_COLORS[theme];
}

function getSnapshot(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Follows the OS until the person picks a theme.
  const query = window.matchMedia("(prefers-color-scheme: light)");
  const onSystemChange = (event: MediaQueryListEvent) => {
    if (readStored()) return;
    apply(event.matches ? "light" : "dark");
    listeners.forEach((notify) => notify());
  };
  query.addEventListener("change", onSystemChange);
  return () => {
    listeners.delete(listener);
    query.removeEventListener("change", onSystemChange);
  };
}

export function setTheme(theme: Theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Best effort; the choice simply won't survive a reload.
  }
  apply(theme);
  listeners.forEach((notify) => notify());
}

export function useTheme(): { theme: Theme; setTheme: (theme: Theme) => void; toggle: () => void } {
  const theme = useSyncExternalStore(subscribe, getSnapshot, () => "dark" as Theme);
  return {
    theme,
    setTheme,
    toggle: () => setTheme(theme === "dark" ? "light" : "dark"),
  };
}
