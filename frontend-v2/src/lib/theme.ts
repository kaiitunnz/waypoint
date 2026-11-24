"use client";

import { useSyncExternalStore } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "waypoint-theme";
// The ground colour of each theme: the browser chrome and overscroll match it.
export const THEME_COLORS: Record<Theme, string> = { dark: "#0b0e13", light: "#eeede9" };

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

// Runs before first paint (inline in <head>), so there is no flash of the wrong
// theme. It also owns the one theme-color meta the provider keeps in sync.
export const ANTI_FLASH_SCRIPT = `(function(){
  var theme='dark';
  try{
    var t=localStorage.getItem('${STORAGE_KEY}');
    if(t==='light'||t==='dark'){theme=t;}
    else if(window.matchMedia('(prefers-color-scheme: light)').matches){theme='light';}
  }catch(e){}
  document.documentElement.dataset.theme=theme;
  var m=document.createElement('meta');
  m.name='theme-color';
  m.content=theme==='light'?'${THEME_COLORS.light}':'${THEME_COLORS.dark}';
  document.head.appendChild(m);
})();`;
