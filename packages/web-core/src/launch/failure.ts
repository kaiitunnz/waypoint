import { CWD_NOT_FOUND_DETAIL, isAuthError, SSH_MASTER_REQUIRED_DETAIL } from "../api";

export interface LaunchFailure {
  // `auth`: the sign-in expired. `cwd`: the folder does not exist. `ssh`: a
  // password-auth SSH target needs connecting first, then the launch is retried.
  kind: "auth" | "cwd" | "ssh" | "detail";
  message: string;
}

export function classifyLaunchFailure(error: unknown, fallback: string): LaunchFailure {
  const message = error instanceof Error && error.message ? error.message : fallback;
  if (isAuthError(error)) return { kind: "auth", message };
  if (message === CWD_NOT_FOUND_DETAIL) return { kind: "cwd", message };
  if (message === SSH_MASTER_REQUIRED_DETAIL) return { kind: "ssh", message };
  return { kind: "detail", message };
}
