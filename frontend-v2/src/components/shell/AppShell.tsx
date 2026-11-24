"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { showsTabBar } from "@waypoint/web-core/shellNav";

import { cx } from "@/lib/cx";

import { NavRail } from "./NavRail";
import { TabBar } from "./TabBar";
import styles from "./AppShell.module.css";

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
