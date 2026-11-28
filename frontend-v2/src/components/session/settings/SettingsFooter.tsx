import type { SessionSettingsController } from "@waypoint/web-core/hooks/useSessionSettings";
import { applyErrorMessage } from "@waypoint/web-core/session/settingsFields";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";

import styles from "./SettingsFooter.module.css";

interface SettingsFooterProps {
  controller: SessionSettingsController;
  onCancel: () => void;
  // Applying went through.
  onApplied: () => void;
}

// What applying will do, a failure if one stopped it, then Cancel and Apply.
export function SettingsFooter({ controller, onCancel, onApplied }: SettingsFooterProps) {
  const { loading, applying, applyError, partialSuccess, plan, applyDisabled } = controller;
  return (
    <div className={styles.footer}>
      {plan.warnings.length > 0 && !loading ? (
        <div className={styles.notes} role="status">
          {plan.warnings.map((warning) => (
            <p key={warning} className={styles.warn}>
              <Icon name="alert" size={16} />
              <span>{warning}</span>
            </p>
          ))}
        </div>
      ) : null}
      {applyError ? (
        <p className={styles.error} role="alert">
          {applyErrorMessage(applyError, partialSuccess)}
        </p>
      ) : null}
      <div className={styles.actions}>
        <Button variant="text" disabled={applying} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={applyDisabled}
          busy={applying}
          onClick={() => {
            void controller.apply().then((ok) => {
              if (ok) onApplied();
            });
          }}
        >
          {applying ? "Applying…" : "Apply"}
        </Button>
      </div>
    </div>
  );
}
