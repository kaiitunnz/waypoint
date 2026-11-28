import type { InspectorAction, InspectorActionId, InspectorActions } from "@waypoint/web-core/session/inspectorActions";

import { Button } from "@/components/ui/Button";
import { Icon, type IconName } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import styles from "./Actions.module.css";

const ICON: Record<InspectorActionId, IconName> = {
  switch: "switch",
  terminal: "terminal",
  chat: "chat",
  files: "files",
  schedule: "clock",
  settings: "settings",
  rename: "pencil",
  pin: "pin",
  unpin: "pin",
  refresh: "refresh",
  reconnect: "reset",
  terminate: "power",
  delete: "trash",
};

interface ActionsProps {
  actions: InspectorActions;
  onAction: (id: InspectorActionId) => void;
}

// The sheet's actions as quiet icon-and-label tiles.
export function ActionTiles({ actions, onAction }: ActionsProps) {
  return (
    <div className={styles.tiles} role="group" aria-label="Actions">
      {actions.main.map((action) => (
        <button key={action.id} type="button" className={styles.tile} onClick={() => onAction(action.id)}>
          <span className={styles.icon}>
            <Icon name={ICON[action.id]} size={21} strokeWidth={1.7} />
          </span>
          {action.label}
        </button>
      ))}
    </div>
  );
}

function EndButton({ action, onAction, icon }: { action: InspectorAction; onAction: ActionsProps["onAction"]; icon: boolean }) {
  return (
    <Button variant="danger" className={styles.end} onClick={() => onAction(action.id)}>
      {icon ? <Icon name={ICON[action.id]} size={17} strokeWidth={1.9} /> : null}
      {action.label}
    </Button>
  );
}

// Reconnect, then the action that ends the session, set apart in red.
export function ActionsFoot({ actions, onAction, surface }: ActionsProps & { surface: "pane" | "sheet" }) {
  const sheet = surface === "sheet";
  return (
    <div className={cx(styles.foot, sheet && styles.sheetFoot)}>
      {sheet ? null : (
        <>
          {actions.main.map((action) => (
            <Button key={action.id} variant="text" size="sm" onClick={() => onAction(action.id)}>
              {action.label}
            </Button>
          ))}
        </>
      )}
      {actions.reconnect ? (
        <Button variant="text" size={sheet ? "md" : "sm"} onClick={() => onAction("reconnect")}>
          {sheet ? <Icon name="reset" size={17} strokeWidth={1.8} /> : null}
          {actions.reconnect.label}
        </Button>
      ) : null}
      <EndButton action={actions.end} onAction={onAction} icon={sheet} />
    </div>
  );
}
