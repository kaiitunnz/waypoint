import { Toggle } from "@/components/ui/Toggle";

import styles from "./FocusRow.module.css";

interface FocusRowProps {
  on: boolean;
  busy: boolean;
  onChange: (on: boolean) => void;
  // A line under the label, on the sheet where the row has room for it.
  detail?: string;
}

// Focus holds incoming messages until the human turns it off.
export function FocusRow({ on, busy, onChange, detail }: FocusRowProps) {
  return (
    <div className={styles.row}>
      <span className={styles.text}>
        <span className={styles.label}>Focus</span>
        {detail ? <span className={styles.detail}>{detail}</span> : null}
      </span>
      <Toggle label="Focus" checked={on} disabled={busy} onChange={onChange} />
    </div>
  );
}
