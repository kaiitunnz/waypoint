import type { ScreenAction } from "@/components/sessions/SessionMenu";

export interface ReconnectAction {
  busy: boolean;
  run: () => void;
}

export interface ScreenMenuOptions {
  // Set while the session is stopped and can be brought back.
  reconnect: ReconnectAction | null;
  // Set while the Terminal view shows.
  terminal: {
    refresh: () => void;
    resume: (() => void) | null;
    // The chat composer's session tools, for a pane shown without it. Each is
    // null where nothing on screen can host it.
    tools: {
      focus: { on: boolean; busy: boolean; toggle: () => void };
      schedule: (() => void) | null;
      files: (() => void) | null;
    } | null;
  } | null;
}

// The header's screen actions and the term bar's.
export function screenMenus({ reconnect, terminal }: ScreenMenuOptions): { session: ScreenAction[]; terminal: ScreenAction[] } {
  const tools = terminal?.tools;
  return {
    session: [
      { id: "refresh", label: "Refresh", run: () => window.location.reload() },
      ...(reconnect
        ? [
            {
              id: "reconnect",
              label: reconnect.busy ? "Reconnecting…" : "Reconnect session",
              disabled: reconnect.busy,
              run: reconnect.run,
            },
          ]
        : []),
    ],
    terminal: terminal
      ? [
          { id: "refresh-pane", label: "Refresh pane", run: terminal.refresh },
          ...(terminal.resume ? [{ id: "resume-pane", label: "Resume pane", run: terminal.resume }] : []),
          ...(tools
            ? [
                {
                  id: "focus",
                  label: tools.focus.on ? "Turn off Focus" : "Turn on Focus",
                  disabled: tools.focus.busy,
                  run: tools.focus.toggle,
                },
              ]
            : []),
          ...(tools?.schedule ? [{ id: "schedule", label: "Schedule message", run: tools.schedule }] : []),
          ...(tools?.files ? [{ id: "files", label: "Session files", run: tools.files }] : []),
        ]
      : [],
  };
}
