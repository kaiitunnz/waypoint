"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { cx } from "@/lib/cx";

import styles from "./Toast.module.css";

export interface ToastOptions {
  message: string;
  tone?: "default" | "error";
  action?: { label: string; onSelect: () => void };
}

interface ToastEntry extends ToastOptions {
  id: number;
}

interface ToastContextValue {
  toast: (options: ToastOptions) => void;
}

const TOAST_DURATION_MS = 4000;

const ToastContext = createContext<ToastContextValue>({ toast: () => {} });

export function useToast(): ToastContextValue {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<ToastEntry[]>([]);
  const next = useRef(0);

  const dismiss = useCallback((id: number) => {
    setEntries((current) => current.filter((entry) => entry.id !== id));
  }, []);

  const toast = useCallback(
    (options: ToastOptions) => {
      const id = next.current++;
      setEntries((current) => [...current.slice(-2), { ...options, id }]);
      setTimeout(() => dismiss(id), TOAST_DURATION_MS);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ toast }), [toast]);
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
                        entry.action?.onSelect();
                        dismiss(entry.id);
                      }}
                    >
                      {entry.action.label}
                    </button>
                  ) : null}
                </div>
              ))}
            </div>,
            document.body,
          )
        : null}
    </ToastContext.Provider>
  );
}
