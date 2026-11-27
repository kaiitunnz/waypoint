"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { activeNavKey, isSettingsPath, NAV_DESTINATIONS, SETTINGS_HREF } from "@waypoint/web-core/shellNav";

import { Badge } from "@/components/ui/Badge";
import { Icon } from "@/components/ui/Icon";
import { Logo } from "@/components/ui/Logo";
import { cx } from "@/lib/cx";
import { useShellData } from "@/lib/shell-data";

import { NavIcon } from "./NavIcon";
import { ThemeToggle } from "./ThemeToggle";
import styles from "./NavRail.module.css";

export function NavRail() {
  const pathname = usePathname();
  const active = activeNavKey(pathname);
  const settings = isSettingsPath(pathname);
  const { waiting } = useShellData();
  return (
    <nav className={styles.rail} aria-label="Main">
      <Link href="/" className={styles.logo} aria-label="Waypoint">
        <Logo />
      </Link>
      {NAV_DESTINATIONS.map((destination) => {
        const isActive = destination.key === active;
        const count = destination.key === "needs-you" ? waiting.all : 0;
        return (
          <Link
            key={destination.key}
            href={destination.href}
            className={cx(styles.item, isActive && styles.on)}
            aria-current={isActive ? "page" : undefined}
            aria-label={count > 0 ? `${destination.label}, ${count} waiting` : destination.label}
            title={destination.label}
          >
            <NavIcon navKey={destination.key} />
            <Badge count={count} ringed className={styles.badge} />
          </Link>
        );
      })}
      <span className={styles.spacer} />
      <Link
        href={SETTINGS_HREF}
        className={cx(styles.item, settings && styles.on)}
        aria-current={settings ? "page" : undefined}
        aria-label="Settings"
        title="Settings"
      >
        <Icon name="settings" size={20} />
      </Link>
      <ThemeToggle />
    </nav>
  );
}
