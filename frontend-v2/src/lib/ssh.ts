import type { HostOnline } from "@waypoint/web-core/hostPicker";
import type { LaunchTargetSummary } from "@waypoint/web-core/types";

// The lamp of an SSH target. Only a live connection says one has been reached:
// nothing checks a key-auth target.
export function sshLamp(target: LaunchTargetSummary | undefined): HostOnline {
  return target?.connected ? "online" : "unknown";
}
