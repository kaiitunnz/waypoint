import type { ProbeStatus } from "@waypoint/web-core/hooks/useBackendProbe";

export const PROBE_LABEL: Record<ProbeStatus, string> = {
  idle: "",
  checking: "Checking",
  reachable: "Reachable",
  unreachable: "Unreachable",
};
