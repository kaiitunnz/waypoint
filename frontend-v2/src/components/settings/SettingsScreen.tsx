"use client";

import { useEffect, useRef, useState } from "react";

import { SETTINGS_HREF, settingsSectionHref } from "@waypoint/web-core/shellNav";

import { PageHeader } from "@/components/ui/PageHeader";
import { SectionHeading } from "@/components/ui/SectionHeading";
import { LinkRow, SettingsSection } from "@/components/ui/Settings";
import { cx } from "@/lib/cx";
import { useMultiColumn, usePhoneLayout } from "@/lib/layout";

import { SETTINGS_SECTIONS, type SettingsScope, type SettingsSectionSpec } from "./sections";
import styles from "./SettingsScreen.module.css";

const SCOPE_TITLES: Record<SettingsScope, string> = { device: "This device", server: "Server" };
const BACK_HOME = { href: "/", label: "Sessions" };

// Sections by scope, in list order. Scopes are named only once there is more than one.
function byScope(sections: SettingsSectionSpec[]): { title?: string; sections: SettingsSectionSpec[] }[] {
  const scopes = [...new Set(sections.map((section) => section.scope))];
  return scopes.map((scope) => ({
    title: scopes.length > 1 ? SCOPE_TITLES[scope] : undefined,
    sections: sections.filter((section) => section.scope === scope),
  }));
}

// A phone opens one section at a time from a list; every other client gets every section
// on one page, with a list of them beside it where there is room.
export function SettingsScreen({ sectionId }: { sectionId?: string }) {
  const phone = usePhoneLayout();
  const columns = useMultiColumn();
  const section = SETTINGS_SECTIONS.find((entry) => entry.id === sectionId);

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
          {byScope(SETTINGS_SECTIONS).map((group) => (
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
          {SETTINGS_SECTIONS.map((entry) => (
            <section key={entry.id} id={entry.id} className={styles.section}>
              <SectionHeading title={entry.title} />
              <entry.Component />
            </section>
          ))}
        </div>
      </div>
    </>
  );
}

function scrollerOf(node: Element): Element {
  for (let parent = node.parentElement; parent; parent = parent.parentElement) {
    const { overflowY } = getComputedStyle(parent);
    if ((overflowY === "auto" || overflowY === "scroll") && parent.scrollHeight > parent.clientHeight) return parent;
  }
  return document.scrollingElement ?? document.documentElement;
}

// The sections, the one in view marked with a fill. A section picked here stays marked
// even when the page is too short to bring it to the top.
function SectionList({ opened }: { opened?: string }) {
  const [current, setCurrent] = useState<string | undefined>(opened ?? SETTINGS_SECTIONS[0]?.id);
  const picked = useRef(0);

  useEffect(() => {
    if (opened) document.getElementById(opened)?.scrollIntoView();
  }, [opened]);

  useEffect(() => {
    const first = document.getElementById(SETTINGS_SECTIONS[0]?.id ?? "");
    if (!first) return;
    const scroller = scrollerOf(first);
    const update = () => {
      if (Date.now() - picked.current < 300) return;
      const top = scroller === document.scrollingElement ? 0 : scroller.getBoundingClientRect().top;
      const atEnd = scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop <= 1;
      const reached = SETTINGS_SECTIONS.filter((entry) => {
        const node = document.getElementById(entry.id);
        return node ? node.getBoundingClientRect().top - top <= 24 : false;
      });
      setCurrent((atEnd ? SETTINGS_SECTIONS.at(-1) : reached.at(-1) ?? SETTINGS_SECTIONS[0])?.id);
    };
    document.addEventListener("scroll", update, { capture: true, passive: true });
    return () => document.removeEventListener("scroll", update, { capture: true });
  }, []);

  return (
    <nav className={styles.list} aria-label="Settings sections">
      {SETTINGS_SECTIONS.map((entry) => (
        <a
          key={entry.id}
          href={`#${entry.id}`}
          className={cx(styles.entry, entry.id === current && styles.on)}
          aria-current={entry.id === current ? "true" : undefined}
          onClick={() => {
            picked.current = Date.now();
            setCurrent(entry.id);
          }}
        >
          {entry.title}
        </a>
      ))}
    </nav>
  );
}
