"use client";

import type { ReactNode } from "react";

import { LoginScreen } from "@/components/login/LoginScreen";
import { SwitcherProvider } from "@/components/sessions/SwitcherProvider";
import { useAuth } from "@/lib/auth";
import { ShellDataProvider } from "@/lib/shell-data";

import { AppShell } from "./AppShell";
import styles from "./AuthGate.module.css";

// Shows the login screen in place of the requested route until there is a token,
// so a deep link survives signing in.
export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  if (!auth.ready) {
    return <div className={styles.splash} />;
  }
  if (!auth.token) {
    return <LoginScreen defaultHost={auth.host} hostName={auth.hostName} notice={auth.notice} />;
  }
  return (
    <ShellDataProvider host={auth.host} token={auth.token}>
      <SwitcherProvider>
        <AppShell>{children}</AppShell>
      </SwitcherProvider>
    </ShellDataProvider>
  );
}
