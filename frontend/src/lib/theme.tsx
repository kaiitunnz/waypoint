"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "waypoint-theme";
const LIGHT_THEME_COLOR = "#f4f1eb";
const DARK_THEME_COLOR = "#06080b";

function readStoredTheme(): Theme | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // localStorage may throw in private mode / locked-down environments.
  }
  return null;
}

function writeStoredTheme(theme: Theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Best-effort; persistence simply won't survive a reload.
  }
}

function resolveInitialTheme(): Theme {
  if (typeof document !== "undefined") {
    // The inline anti-flash script in layout.tsx has already resolved this
    // synchronously and applied it to <html>; mirror it so React's first
    // render matches the DOM and consumers (e.g. ThemeToggle) don't show a
    // mismatched icon for one frame.
    const dataset = document.documentElement.dataset.theme;
    if (dataset === "light" || dataset === "dark") return dataset;
  }
  if (typeof window !== "undefined") {
    const stored = readStoredTheme();
    if (stored) return stored;
    if (window.matchMedia("(prefers-color-scheme: light)").matches) return "light";
  }
  return "dark";
}

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "theme-color";
    document.head.appendChild(meta);
  }
  meta.content = theme === "light" ? LIGHT_THEME_COLOR : DARK_THEME_COLOR;
}

interface ThemeContextValue {
  theme: Theme;
  toggle: () => void;
}

export const ThemeContext = createContext<ThemeContextValue>({
  theme: "dark",
  toggle: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(resolveInitialTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onMqChange = (e: MediaQueryListEvent) => {
      if (readStoredTheme()) return;
      setTheme(e.matches ? "light" : "dark");
    };
    mq.addEventListener("change", onMqChange);
    return () => mq.removeEventListener("change", onMqChange);
  }, []);

  // Read latest via ref so the updater stays pure (no side effects
  // inside setState — strict mode would otherwise persist twice in dev).
  const themeRef = useRef(theme);
  useEffect(() => {
    themeRef.current = theme;
  }, [theme]);

  const toggle = useCallback(() => {
    const next: Theme = themeRef.current === "dark" ? "light" : "dark";
    writeStoredTheme(next);
    setTheme(next);
  }, []);

  const value = useMemo<ThemeContextValue>(() => ({ theme, toggle }), [theme, toggle]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
