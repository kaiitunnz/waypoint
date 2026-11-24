"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { showsTabBar } from "@waypoint/web-core/shellNav";

import { cx } from "@/lib/cx";

import { NavRail } from "./NavRail";
import { TabBar } from "./TabBar";
import styles from "./AppShell.module.css";

// The chrome around every screen: the nav rail on desktop, and the tab bar on
// mobile screens that are top-level. The shell stays mounted across routes.
export function AppShell({ children }: { children: ReactNode }) {
  const tabBar = showsTabBar(usePathname());
  return (
    <div className={styles.app}>
      <NavRail />
      <main className={cx(styles.content, tabBar && styles.withTabBar)}>{children}</main>
      {tabBar ? <TabBar /> : null}
    </div>
  );
}
