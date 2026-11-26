"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { cx } from "@/lib/cx";

import { Icon } from "./Icon";
import styles from "./Toast.module.css";

export interface ToastOptions {
  message: ReactNode;
  tone?: "default" | "error";
  // An action that returns false leaves its toast up.
  action?: { label: string; onSelect: () => boolean | void };
  // A toast with a key replaces the one showing under the same key.
  key?: string;
  // Milliseconds on screen; null keeps it until it is dismissed, as an error does by default.
  duration?: number | null;
}

interface ToastEntry extends ToastOptions {
  id: number;
}

interface ToastContextValue {
  toast: (options: ToastOptions) => void;
  // Closes the toast showing under a key.
  dismiss: (key: string) => void;
}

const TOAST_DURATION_MS = 4000;
const MAX_TOASTS = 4;

const ToastContext = createContext<ToastContextValue>({ toast: () => {}, dismiss: () => {} });

export function useToast(): ToastContextValue {
  return useContext(ToastContext);
}

// The oldest timed toasts give way first, then the oldest sticky ones.
function capped(entries: ToastEntry[]): ToastEntry[] {
  const next = [...entries];
  while (next.length > MAX_TOASTS) {
    const timed = next.findIndex((entry) => entry.duration !== null);
    next.splice(timed === -1 ? 0 : timed, 1);
  }
  return next;
}

// A toast with a key replaces its key's; an unkeyed error replaces the same error.
function replaces(entry: ToastEntry, options: ToastOptions): boolean {
  if (options.key !== undefined) return entry.key === options.key;
  return options.tone === "error" && entry.tone === "error" && entry.key === undefined && entry.message === options.message;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<ToastEntry[]>([]);
  const next = useRef(0);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const clearTimer = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const remove = useCallback(
    (match: (entry: ToastEntry) => boolean) => {
      setEntries((current) => {
        const gone = current.filter(match);
        if (gone.length === 0) return current;
        gone.forEach((entry) => clearTimer(entry.id));
        return current.filter((entry) => !gone.includes(entry));
      });
    },
    [clearTimer],
  );
  const dismissId = useCallback((id: number) => remove((entry) => entry.id === id), [remove]);
  const dismiss = useCallback((key: string) => remove((entry) => entry.key === key), [remove]);

  const toast = useCallback(
    (options: ToastOptions) => {
      const id = next.current++;
      // An error waits for the person to dismiss it.
      const duration = options.duration !== undefined ? options.duration : options.tone === "error" ? null : TOAST_DURATION_MS;
      setEntries((current) => {
        const replaced = current.filter((entry) => replaces(entry, options));
        replaced.forEach((entry) => clearTimer(entry.id));
        return capped([...current.filter((entry) => !replaced.includes(entry)), { ...options, duration, id }]);
      });
      if (duration !== null) {
        timers.current.set(id, setTimeout(() => dismissId(id), duration));
      }
    },
    [clearTimer, dismissId],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((timer) => clearTimeout(timer));
  }, []);

  const value = useMemo(() => ({ toast, dismiss }), [toast, dismiss]);
  // The portal waits for the browser, so hydration never sees it.
  const mounted = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
  return (
    <ToastContext.Provider value={value}>
      {children}
      {mounted
        ? createPortal(
            // Always present, so assistive tech is already watching when a toast arrives.
            <div className={styles.viewport} role="status" aria-live="polite">
              {entries.map((entry) => (
                <div key={entry.id} className={cx(styles.toast, entry.tone === "error" && styles.error)}>
                  <span className={styles.message}>{entry.message}</span>
                  {entry.action ? (
                    <button
                      type="button"
                      className={styles.action}
                      onClick={() => {
                        if (entry.action?.onSelect() !== false) dismissId(entry.id);
                      }}
                    >
                      {entry.action.label}
                    </button>
                  ) : null}
                  <button type="button" className={styles.close} aria-label="Dismiss" title="Dismiss" onClick={() => dismissId(entry.id)}>
                    <Icon name="close" size={16} />
                  </button>
                </div>
              ))}
            </div>,
            document.body,
          )
        : null}
    </ToastContext.Provider>
  );
}
