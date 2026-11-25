import { describe, expect, it } from "vitest";

import type { SessionRecord } from "../types";
import {
  connectionLabel,
  connectionTitle,
  isAgentBusy,
  reconnectDelay,
  type ConnectionState,
} from "./connection";

describe("reconnectDelay", () => {
  it("doubles from one second up to fifteen", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(reconnectDelay)).toEqual([1000, 2000, 4000, 8000, 15000, 15000, 15000]);
  });
});

type Status = SessionRecord["status"];
const session = (status: Status) => ({ status }) as SessionRecord;

describe("isAgentBusy", () => {
  it("is true only for a running session on an open socket", () => {
    expect(isAgentBusy(session("running"), "open")).toBe(true);
    expect(isAgentBusy(session("running"), "connecting")).toBe(false);
    expect(isAgentBusy(session("running"), "reconnecting")).toBe(false);
    expect(isAgentBusy(session("idle"), "open")).toBe(false);
    expect(isAgentBusy(session("waiting_input"), "open")).toBe(false);
  });
});

describe("connectionLabel", () => {
  it("prefers the socket state over the session status", () => {
    expect(connectionLabel("connecting", "running")).toBe("Connecting");
    expect(connectionLabel("reconnecting", "idle")).toBe("Reconnecting");
  });

  it.each([
    ["running", "Running"],
    ["waiting_input", "Waiting on you"],
    ["interrupted", "Interrupted"],
    ["error", "Error"],
    ["exited", "Exited"],
    ["starting", "Starting"],
    ["idle", "Live"],
  ] as const)("labels status %s as %j", (status, label) => {
    expect(connectionLabel("open", status)).toBe(label);
  });

  it("falls back to Live for an unrecognised status", () => {
    expect(connectionLabel("open", "mystery" as Status)).toBe("Live");
  });
});

describe("connectionTitle", () => {
  it("describes the socket when it is not open", () => {
    const states: ConnectionState[] = ["connecting", "reconnecting"];
    for (const state of states) {
      expect(connectionTitle(state, "idle")).toBe(`Socket ${state}`);
    }
  });

  it("describes the session when the socket is open, replacing only the first underscore", () => {
    expect(connectionTitle("open", "running")).toBe("Socket open · session running");
    expect(connectionTitle("open", "waiting_input")).toBe("Socket open · session waiting input");
  });
});
