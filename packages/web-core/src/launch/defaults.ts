import { launchableAgents, type BackendCatalog } from "../backends";
import type { AccountProfile, Backend, BackendDescriptor, LaunchTargetSummary } from "../types";

export interface LaunchDefaults {
  // Agents the host or target registers, including folded transports.
  supportedBackends: Backend[];
  // The agents a person can pick at launch.
  launchableBackends: Backend[];
  defaultBackend: Backend;
  defaultCwd: string;
  defaultLaunchEnvByBackend: Record<Backend, Record<string, string>>;
  accountProfilesByBackend: Record<Backend, AccountProfile[]>;
}

// Launch defaults for this host, or for the active SSH target, whose own agents,
// defaults, environment and account profiles take precedence.
export function launchDefaults({
  registeredBackends,
  descriptors,
  hostDefaultBackend,
  hostDefaultCwd,
  activeTarget,
  catalog,
}: {
  registeredBackends: Backend[];
  descriptors: BackendDescriptor[];
  hostDefaultBackend: Backend;
  hostDefaultCwd: string;
  activeTarget: LaunchTargetSummary | null;
  catalog: BackendCatalog;
}): LaunchDefaults {
  const supportedBackends = activeTarget?.supported_backends.length ? activeTarget.supported_backends : registeredBackends;
  const launchableBackends = launchableAgents(supportedBackends, catalog);
  const preferred = activeTarget?.default_backend ?? hostDefaultBackend;
  const defaultBackend = launchableBackends.includes(preferred)
    ? preferred
    : (launchableBackends[0] ?? supportedBackends[0]);

  const defaultLaunchEnvByBackend: Record<Backend, Record<string, string>> = {};
  const accountProfilesByBackend: Record<Backend, AccountProfile[]> = {};
  for (const descriptor of descriptors) {
    defaultLaunchEnvByBackend[descriptor.id] = { ...(descriptor.default_launch_env ?? {}) };
    accountProfilesByBackend[descriptor.id] = descriptor.account_profiles ?? [];
  }
  for (const [backend, env] of Object.entries(activeTarget?.default_launch_env_by_backend ?? {})) {
    defaultLaunchEnvByBackend[backend] = { ...env };
  }
  for (const [backend, profiles] of Object.entries(activeTarget?.account_profiles_by_backend ?? {})) {
    accountProfilesByBackend[backend] = profiles;
  }

  return {
    supportedBackends,
    launchableBackends,
    defaultBackend,
    defaultCwd: activeTarget?.default_cwd ?? hostDefaultCwd,
    defaultLaunchEnvByBackend,
    accountProfilesByBackend,
  };
}

// The agents whose resumable threads to list: launchable ones that discover
// threads. An agent missing from the catalog is tried, so a first load before the
// catalog arrives still fetches.
export function threadDiscoveryBackends(launchable: Backend[], catalog: BackendCatalog): Backend[] {
  return launchable.filter((id) => catalog.byId(id)?.capabilities?.supports_thread_discovery ?? true);
}
