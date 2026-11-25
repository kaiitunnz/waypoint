"use client";

import { useEffect, useRef } from "react";

import { HostSwitcher } from "@/components/hosts/HostSwitcher";
import { Button } from "@/components/ui/Button";
import { SearchInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import styles from "./HomeHeader.module.css";

interface HomeHeaderProps {
  phone: boolean;
  query: string;
  onQuery: (query: string) => void;
  // Phone: whether the search field is showing.
  searchOpen: boolean;
  onSearchOpen: (open: boolean) => void;
  onNew: () => void;
  // Touch: tapping the title opens the session switcher.
  onTitle: () => void;
}

export function HomeHeader({
  phone,
  query,
  onQuery,
  searchOpen,
  onSearchOpen,
  onNew,
  onTitle,
}: HomeHeaderProps) {
  const pointer = usePointerUI();
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (searchOpen && phone) field.current?.focus();
  }, [searchOpen, phone]);

  const search = (
    <SearchInput
      ref={field}
      className={styles.search}
      value={query}
      placeholder="Search sessions"
      aria-label="Search sessions"
      onChange={(event) => onQuery(event.target.value)}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        onQuery("");
        if (phone) onSearchOpen(false);
      }}
    />
  );

  return (
    <>
      <header className={cx(styles.header, !phone && styles.full)}>
        <h1 className={styles.title}>
          {pointer ? (
            "Sessions"
          ) : (
            <button type="button" className={styles.titleButton} aria-haspopup="dialog" onClick={onTitle}>
              Sessions
            </button>
          )}
        </h1>
        <HostSwitcher />
        {phone ? null : search}
        <span className={styles.grow} />
        {phone ? (
          <IconButton
            label={searchOpen ? "Close search" : "Search sessions"}
            onClick={() => {
              if (searchOpen) onQuery("");
              onSearchOpen(!searchOpen);
            }}
          >
            <Icon name={searchOpen ? "close" : "search"} size={21} />
          </IconButton>
        ) : null}
        <Button variant="primary" onClick={onNew}>
          <Icon name="plus" size={18} strokeWidth={2.2} />
          {phone ? "New" : "New session"}
        </Button>
      </header>
      {phone && searchOpen ? <div className={styles.searchRow}>{search}</div> : null}
    </>
  );
}
