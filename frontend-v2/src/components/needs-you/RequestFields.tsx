import { requestText } from "@waypoint/web-core/session/wholeRequest";

import { CommandText } from "@/components/session/cards/CommandText";

import styles from "./RequestFields.module.css";

// Every field of a request as the agent sent it, so nothing a view leaves out goes unseen.
export function RequestFields({ fields }: { fields: Record<string, unknown> }) {
  return (
    <div className={styles.fields} role="group" aria-label="Whole request" tabIndex={0}>
      <CommandText text={requestText(fields)} whole />
    </div>
  );
}
