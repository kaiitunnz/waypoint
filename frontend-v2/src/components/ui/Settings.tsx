import Link from "next/link";
import type { ReactNode } from "react";

import { cx } from "@/lib/cx";

import { Icon } from "./Icon";
import { SegmentedControl, type Segment } from "./SegmentedControl";
import { Toggle } from "./Toggle";
import styles from "./Settings.module.css";

// A group of settings rows, with hairlines between rows and none around the group.
export function SettingsSection({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <section className={styles.section}>
      {title ? <h3 className={styles.heading}>{title}</h3> : null}
      <div className={styles.rows}>{children}</div>
    </section>
  );
}

interface SettingsRowProps {
  label: ReactNode;
  // The current value, read-only, before the chevron or the control.
  detail?: ReactNode;
  control?: ReactNode;
  // A row that opens something ends in a chevron.
  href?: string;
  onNavigate?: () => void;
}

// The one row every setting uses: a label, then a value, a control or a chevron.
export function SettingsRow({ label, detail, control, href, onNavigate }: SettingsRowProps) {
  const content = (
    <>
      <span className={styles.label}>{label}</span>
      {detail ? <span className={styles.detail}>{detail}</span> : null}
      {control}
      {href ? (
        <span className={styles.chev} aria-hidden="true">
          <Icon name="chevron-right" size={16} strokeWidth={2} />
        </span>
      ) : null}
    </>
  );
  return href ? (
    <Link href={href} className={cx(styles.row, styles.link)} onClick={onNavigate}>
      {content}
    </Link>
  ) : (
    <div className={styles.row}>{content}</div>
  );
}

// A whole row that turns a setting on or off.
export function SwitchRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    // The label activates its switch, so a press anywhere on the row toggles it once.
    <label className={cx(styles.row, styles.link)}>
      <span className={styles.label}>{label}</span>
      <Toggle label={label} checked={checked} onChange={onChange} />
    </label>
  );
}

export function PickerRow<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: Segment<T>[];
  onChange: (value: T) => void;
}) {
  return <SettingsRow label={label} control={<SegmentedControl label={label} value={value} onChange={onChange} segments={options} />} />;
}

export function LinkRow({ label, detail, href, onNavigate }: { label: string; detail?: ReactNode; href: string; onNavigate?: () => void }) {
  return <SettingsRow label={label} detail={detail} href={href} onNavigate={onNavigate} />;
}
