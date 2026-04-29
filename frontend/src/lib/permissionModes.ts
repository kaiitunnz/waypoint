import { Backend } from "@/lib/types";

export interface PermissionModeOption {
  value: string;
  label: string;
}

export const CLAUDE_PERMISSION_MODES: ReadonlyArray<PermissionModeOption> = [
  { value: "default", label: "Default" },
  { value: "plan", label: "Plan" },
  { value: "acceptEdits", label: "Accept Edits" },
  { value: "auto", label: "Auto" },
  { value: "bypassPermissions", label: "Bypass Permissions" },
  { value: "dontAsk", label: "Don't Ask" },
];

export const CODEX_PERMISSION_MODES: ReadonlyArray<PermissionModeOption> = [
  { value: "default", label: "Default" },
  { value: "auto_review", label: "Auto-review" },
  { value: "full_access", label: "Full Access" },
];

export function modesForBackend(
  backend: Backend | string,
): ReadonlyArray<PermissionModeOption> {
  if (backend === "claude_code") return CLAUDE_PERMISSION_MODES;
  if (backend === "codex") return CODEX_PERMISSION_MODES;
  return [];
}

export function permissionModeLabel(
  backend: Backend | string,
  value: string | null | undefined,
): string | null {
  if (!value) return null;
  const match = modesForBackend(backend).find((mode) => mode.value === value);
  return match?.label ?? value;
}
