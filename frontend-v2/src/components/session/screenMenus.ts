import type { ScreenAction } from "@/components/sessions/SessionMenu";

export interface ScreenMenuOptions {
  // Set while the session is stopped and can be brought back.
  reconnect: { busy: boolean; run: () => void } | null;
  // Set while the Terminal view shows.
  terminal: {
    refresh: () => void;
    // Set while the pane is up but its agent is not running.
    resume: (() => void) | null;
    // The chat composer's session tools, for a pane shown without it.
    tools: {
      focus: { on: boolean; busy: boolean; toggle: () => void };
      schedule: () => void;
      // Set where the session takes files.
      files: (() => void) | null;
    } | null;
  } | null;
}

export interface ScreenMenus {
  // The header's menu, after the session's own actions.
  session: ScreenAction[];
  // The term bar's menu.
  terminal: ScreenAction[];
}

// Every screen action is defined once; each menu lists its own.
export function screenMenus({ reconnect, terminal }: ScreenMenuOptions): ScreenMenus {
  const tools = terminal?.tools ?? null;
  const entries = {
    refresh: { id: "refresh", label: "Refresh", run: () => window.location.reload() },
    reconnect: reconnect && {
      id: "reconnect",
      label: reconnect.busy ? "Reconnecting…" : "Reconnect session",
      disabled: reconnect.busy,
      run: reconnect.run,
    },
    refreshPane: terminal && { id: "refresh-pane", label: "Refresh pane", run: terminal.refresh },
    resumePane: terminal?.resume ? { id: "resume-pane", label: "Resume pane", run: terminal.resume } : null,
    focus: tools && {
      id: "focus",
      label: tools.focus.on ? "Turn off Focus" : "Turn on Focus",
      disabled: tools.focus.busy,
      run: tools.focus.toggle,
    },
    schedule: tools && { id: "schedule", label: "Schedule message", run: tools.schedule },
    files: tools?.files ? { id: "files", label: "Session files", run: tools.files } : null,
  } satisfies Record<string, ScreenAction | null>;
  const present = (items: (ScreenAction | null)[]) => items.filter((item): item is ScreenAction => item !== null);
  return {
    session: present([entries.refresh, entries.reconnect]),
    terminal: present([entries.refreshPane, entries.resumePane, entries.focus, entries.schedule, entries.files]),
  };
}
