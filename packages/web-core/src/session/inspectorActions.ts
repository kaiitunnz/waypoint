import type { SessionRecord } from "../types";

export type InspectorActionId =
  | "switch"
  | "terminal"
  | "chat"
  | "files"
  | "schedule"
  | "settings"
  | "rename"
  | "pin"
  | "unpin"
  | "refresh"
  | "reconnect"
  | "terminate"
  | "delete";

export interface InspectorAction {
  id: InspectorActionId;
  label: string;
  // The action ends the session or deletes it.
  danger?: boolean;
}

// The one place that decides how a session is ended: a stopped one is deleted, any
// other terminated.
export function sessionEndAction(session: Pick<SessionRecord, "status">): "terminate" | "delete" {
  return session.status === "exited" ? "delete" : "terminate";
}

export interface InspectorActionOptions {
  // The sheet lists every action; the side pane keeps the few its header menu lacks.
  surface: "pane" | "sheet";
  session: Pick<SessionRecord, "status" | "pinned_at">;
  // The session mirrors its terminal, so the Chat/Terminal switch is offered.
  hasPane: boolean;
  // The session is only a terminal, with no chat to switch to.
  terminalOnly: boolean;
  view: "chat" | "terminal";
  // A composer is mounted, which a scheduled message needs.
  composing: boolean;
  // The session is stopped and can be brought back.
  dormant: boolean;
  hasFiles: boolean;
}

export interface InspectorActions {
  // The actions of the main block: tiles on the sheet, text buttons in the pane.
  main: InspectorAction[];
  reconnect: InspectorAction | null;
  // Set apart from the rest.
  end: InspectorAction;
}

export function inspectorActions(options: InspectorActionOptions): InspectorActions {
  const { surface, session, hasPane, terminalOnly, view, composing, dormant, hasFiles } = options;
  const sheet = surface === "sheet";
  const end = sessionEndAction(session);
  const main: (InspectorAction | null)[] = [
    sheet ? { id: "switch", label: "Switch" } : null,
    sheet && hasPane && !terminalOnly ? (view === "terminal" ? { id: "chat", label: "Chat" } : { id: "terminal", label: "Terminal" }) : null,
    sheet && hasFiles ? { id: "files", label: "Session files" } : null,
    composing ? { id: "schedule", label: sheet ? "Schedule" : "Schedule message" } : null,
    { id: "settings", label: "Settings" },
    sheet ? { id: "rename", label: "Rename" } : null,
    sheet ? (session.pinned_at ? { id: "unpin", label: "Unpin" } : { id: "pin", label: "Pin" }) : null,
    sheet ? { id: "refresh", label: "Refresh" } : null,
  ];
  return {
    main: main.filter((action): action is InspectorAction => action !== null),
    reconnect: dormant ? { id: "reconnect", label: "Reconnect" } : null,
    end:
      end === "delete"
        ? { id: "delete", label: "Delete transcript", danger: true }
        : { id: "terminate", label: sheet ? "Terminate session" : "Terminate", danger: true },
  };
}
