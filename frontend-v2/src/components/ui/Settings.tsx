import Link from "next/link";
import type { ReactNode, Ref } from "react";

import { cx } from "@/lib/cx";

import { Icon } from "./Icon";
import { SegmentedControl, type Segment } from "./SegmentedControl";
import { Toggle } from "./Toggle";
import styles from "./Settings.module.css";

export function SettingsSection({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <section>
      {title ? <h3 className={styles.heading}>{title}</h3> : null}
      <div className={styles.rows}>{children}</div>
    </section>
  );
}

interface SettingsRowProps {
  label: ReactNode;
  // The current value, read-only.
  detail?: ReactNode;
  control?: ReactNode;
  href?: string;
  onNavigate?: () => void;
  // Opens a picker or editor over the current view; the row then ends in a chevron too.
  onPress?: () => void;
  // The detail is a machine string, such as a path.
  mono?: boolean;
  disabled?: boolean;
  // The row's value is changed and not yet applied.
  staged?: boolean;
  // The button of a pressable row, for a popover that anchors to it.
  buttonRef?: Ref<HTMLButtonElement>;
}

export function SettingsRow({ label, detail, control, href, onNavigate, onPress, mono, disabled, staged, buttonRef }: SettingsRowProps) {
  const content = (
    <>
      <span className={styles.label}>{label}</span>
      {staged ? <i className={styles.staged} role="img" aria-label="Changed" /> : null}
      {detail ? <span className={cx(styles.detail, mono && styles.mono)}>{detail}</span> : null}
      {control}
      {href || onPress ? (
        <span className={styles.chev} aria-hidden="true">
          <Icon name="chevron-right" size={16} strokeWidth={2} />
        </span>
      ) : null}
    </>
  );
  if (onPress) {
    return (
      <button ref={buttonRef} type="button" className={cx(styles.row, styles.pressable, styles.button)} onClick={onPress} disabled={disabled}>
        {content}
      </button>
    );
  }
  return href ? (
    <Link href={href} className={cx(styles.row, styles.pressable)} onNavigate={onNavigate}>
      {content}
    </Link>
  ) : (
    <div className={styles.row}>{content}</div>
  );
}

export function SwitchRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    // A label, so a press anywhere on the row toggles the switch once.
    <label className={cx(styles.row, styles.pressable)}>
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
