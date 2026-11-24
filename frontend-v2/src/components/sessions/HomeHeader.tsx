"use client";

import { useEffect, useRef } from "react";

import { HostSwitcher } from "@/components/hosts/HostSwitcher";
import { Button } from "@/components/ui/Button";
import { SearchInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";

import styles from "./HomeHeader.module.css";

interface HomeHeaderProps {
  desktop: boolean;
  query: string;
  onQuery: (query: string) => void;
  // Mobile: whether the search field is showing.
  searchOpen: boolean;
  onSearchOpen: (open: boolean) => void;
  onNew: () => void;
  // Mobile: tapping the title opens the session switcher.
  onTitle: () => void;
}

export function HomeHeader({
  desktop,
  query,
  onQuery,
  searchOpen,
  onSearchOpen,
  onNew,
  onTitle,
}: HomeHeaderProps) {
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (searchOpen && !desktop) field.current?.focus();
  }, [searchOpen, desktop]);

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
        if (!desktop) onSearchOpen(false);
      }}
    />
  );

  return (
    <>
      <header className={styles.header}>
        <h1 className={styles.title}>
          {desktop ? (
            "Sessions"
          ) : (
            <button type="button" className={styles.titleButton} aria-haspopup="dialog" onClick={onTitle}>
              Sessions
            </button>
          )}
        </h1>
        <HostSwitcher />
        {desktop ? search : null}
        <span className={styles.grow} />
        {desktop ? null : (
          <IconButton
            label={searchOpen ? "Close search" : "Search sessions"}
            onClick={() => {
              if (searchOpen) onQuery("");
              onSearchOpen(!searchOpen);
            }}
          >
            <Icon name={searchOpen ? "close" : "search"} size={21} />
          </IconButton>
        )}
        <Button variant="primary" onClick={onNew}>
          <Icon name="plus" size={18} strokeWidth={2.2} />
          {desktop ? "New session" : "New"}
        </Button>
      </header>
      {desktop || !searchOpen ? null : <div className={styles.searchRow}>{search}</div>}
    </>
  );
}
