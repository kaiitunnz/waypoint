"use client";

import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";

import { SETTINGS_HREF, settingsSectionHref } from "@waypoint/web-core/shellNav";

import { PageHeader } from "@/components/ui/PageHeader";
import { LinkRow, SettingsSection } from "@/components/ui/Settings";
import { cx } from "@/lib/cx";
import { useMultiColumn, usePhoneLayout } from "@/lib/layout";

import { settingsSection, SETTINGS_SECTIONS, type SettingsScope, type SettingsSectionSpec } from "./sections";
import styles from "./SettingsScreen.module.css";

const SCOPE_TITLES: Record<SettingsScope, string> = { device: "This device", server: "Server" };
const BACK_HOME = { href: "/", label: "Sessions" };

// Scopes are named only once there is more than one.
function byScope(sections: SettingsSectionSpec[]): { title?: string; sections: SettingsSectionSpec[] }[] {
  const scopes = [...new Set(sections.map((section) => section.scope))];
  return scopes.map((scope) => ({
    title: scopes.length > 1 ? SCOPE_TITLES[scope] : undefined,
    sections: sections.filter((section) => section.scope === scope),
  }));
}

const GROUPS = byScope(SETTINGS_SECTIONS);

export function SettingsScreen({ sectionId }: { sectionId?: string }) {
  const phone = usePhoneLayout();
  const columns = useMultiColumn();
  const section = sectionId === undefined ? undefined : settingsSection(sectionId);
  // Every section is on the page, so a section's path opens the page at it.
  useEffect(() => {
    const node = !phone && sectionId ? document.getElementById(sectionId) : null;
    if (node) scrollToSection(node);
  }, [phone, sectionId]);

  if (phone && section) {
    return (
      <>
        <PageHeader title={section.title} back={{ href: SETTINGS_HREF, label: "Settings" }} />
        <div className={styles.page}>
          <section.Component />
        </div>
      </>
    );
  }
  if (phone) {
    return (
      <>
        <PageHeader title="Settings" back={BACK_HOME} />
        <div className={styles.page}>
          {GROUPS.map((group) => (
            <SettingsSection key={group.title ?? "all"} title={group.title}>
              {group.sections.map((entry) => (
                <LinkRow key={entry.id} label={entry.title} href={settingsSectionHref(entry.id)} />
              ))}
            </SettingsSection>
          ))}
        </div>
      </>
    );
  }
  return (
    <>
      <PageHeader title="Settings" back={BACK_HOME} />
      <div className={cx(styles.page, columns && styles.columns)}>
        {columns ? <SectionList opened={section?.id} /> : null}
        <div>
          {GROUPS.map((group) => (
            <div key={group.title ?? "all"}>
              {group.title ? <p className={styles.scope}>{group.title}</p> : null}
              {group.sections.map((entry) => (
                <section key={entry.id} id={entry.id} className={styles.section}>
                  <h2 className={styles.title}>{entry.title}</h2>
                  <entry.Component />
                </section>
              ))}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function scrollerOf(node: Element): Element {
  for (let parent = node.parentElement; parent; parent = parent.parentElement) {
    const { overflowY } = getComputedStyle(parent);
    if (overflowY === "auto" || overflowY === "scroll") return parent;
  }
  return document.scrollingElement ?? document.documentElement;
}

const scrollerTop = (scroller: Element) => (scroller === document.scrollingElement ? 0 : scroller.getBoundingClientRect().top);

// Moves only the page's own scroller; scrollIntoView would also move the clipped shell.
function scrollToSection(node: Element) {
  const scroller = scrollerOf(node);
  scroller.scrollTop += node.getBoundingClientRect().top - scrollerTop(scroller) - parseFloat(getComputedStyle(node).scrollMarginTop);
}

// A section picked here stays marked even when the page is too short to bring it to the top.
function SectionList({ opened }: { opened?: string }) {
  const [current, setCurrent] = useState(opened ?? SETTINGS_SECTIONS[0].id);
  const list = useRef<HTMLElement>(null);
  const picked = useRef(0);

  useEffect(() => {
    if (!list.current) return;
    const scroller = scrollerOf(list.current);
    const update = () => {
      if (Date.now() - picked.current < 300) return;
      const top = scrollerTop(scroller);
      const atEnd = scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop <= 1;
      const reached = SETTINGS_SECTIONS.filter((entry) => {
        const node = document.getElementById(entry.id);
        return node ? node.getBoundingClientRect().top - top <= 24 : false;
      });
      setCurrent((atEnd ? SETTINGS_SECTIONS.at(-1) : reached.at(-1))?.id ?? SETTINGS_SECTIONS[0].id);
    };
    document.addEventListener("scroll", update, { capture: true, passive: true });
    return () => document.removeEventListener("scroll", update, { capture: true });
  }, []);

  const pick = useCallback((event: MouseEvent, id: string) => {
    const node = document.getElementById(id);
    if (!node || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    picked.current = Date.now();
    setCurrent(id);
    scrollToSection(node);
    window.history.replaceState(null, "", settingsSectionHref(id));
  }, []);

  return (
    <nav ref={list} className={styles.list} aria-label="Settings sections">
      {GROUPS.map((group) => (
        <div key={group.title ?? "all"} className={styles.group}>
          {group.title ? <p className={styles.scope}>{group.title}</p> : null}
          {group.sections.map((entry) => (
            <a
              key={entry.id}
              href={settingsSectionHref(entry.id)}
              className={cx(styles.entry, entry.id === current && styles.on)}
              aria-current={entry.id === current ? "true" : undefined}
              onClick={(event) => pick(event, entry.id)}
            >
              {entry.title}
            </a>
          ))}
        </div>
      ))}
    </nav>
  );
}
