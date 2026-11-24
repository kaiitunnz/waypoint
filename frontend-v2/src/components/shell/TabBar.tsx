"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { activeNavKey, NAV_DESTINATIONS } from "@waypoint/web-core/shellNav";

import { Badge } from "@/components/ui/Badge";
import { cx } from "@/lib/cx";
import { useDesktop } from "@/lib/layout";
import { useShellData } from "@/lib/shell-data";

import { NavIcon } from "./NavIcon";
import styles from "./TabBar.module.css";

export function TabBar() {
  const active = activeNavKey(usePathname());
  const { needsYou } = useShellData();
  const desktop = useDesktop();
  // Toasts sit above the tab bar while it is on screen.
  useEffect(() => {
    if (desktop) return;
    const root = document.documentElement;
    root.style.setProperty("--dock-height", "var(--tabbar-height)");
    return () => {
      root.style.removeProperty("--dock-height");
    };
  }, [desktop]);
  return (
    <nav className={styles.bar} aria-label="Main">
      {NAV_DESTINATIONS.map((destination) => {
        const isActive = destination.key === active;
        const count = destination.key === "needs-you" ? needsYou : 0;
        return (
          <Link
            key={destination.key}
            href={destination.href}
            className={cx(styles.tab, isActive && styles.on)}
            aria-current={isActive ? "page" : undefined}
            aria-label={count > 0 ? `${destination.label}, ${count} waiting` : undefined}
          >
            <NavIcon navKey={destination.key} />
            <span>{destination.label}</span>
            <Badge count={count} ringed className={styles.badge} />
          </Link>
        );
      })}
    </nav>
  );
}
