"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { cx } from "@/lib/cx";

import styles from "./Toast.module.css";

export interface ToastOptions {
  message: string;
  tone?: "default" | "error";
  action?: { label: string; onSelect: () => void };
  // Milliseconds; 0 keeps it until dismissed.
  duration?: number;
}

interface ToastEntry extends ToastOptions {
  id: number;
}

interface ToastContextValue {
  toast: (options: ToastOptions) => void;
}

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
      const duration = options.duration ?? 4000;
      if (duration > 0) {
        setTimeout(() => dismiss(id), duration);
      }
    },
    [dismiss],
  );

  const value = useMemo(() => ({ toast }), [toast]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      {typeof document !== "undefined" && entries.length > 0
        ? createPortal(
            <div className={styles.viewport}>
              {entries.map((entry) => (
                <div
                  key={entry.id}
                  role={entry.tone === "error" ? "alert" : "status"}
                  className={cx(styles.toast, entry.tone === "error" && styles.error)}
                >
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
