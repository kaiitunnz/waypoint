import { CWD_NOT_FOUND_DETAIL, isAuthError, SSH_MASTER_REQUIRED_DETAIL } from "../api";

export type LaunchFailure =
  // The sign-in expired.
  | { kind: "auth" }
  // The folder does not exist; shown on the folder field.
  | { kind: "cwd" }
  // A password-auth SSH target needs connecting first, then the launch is retried.
  | { kind: "ssh" }
  | { kind: "detail"; message: string };

export function classifyLaunchFailure(error: unknown, fallback: string): LaunchFailure {
  if (isAuthError(error)) return { kind: "auth" };
  if (error instanceof Error && error.message === CWD_NOT_FOUND_DETAIL) return { kind: "cwd" };
  if (error instanceof Error && error.message === SSH_MASTER_REQUIRED_DETAIL) return { kind: "ssh" };
  return { kind: "detail", message: error instanceof Error && error.message ? error.message : fallback };
}
