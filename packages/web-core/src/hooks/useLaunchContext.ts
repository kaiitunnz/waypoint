"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createSessionPreset,
  deleteSessionPreset,
  fetchMe,
  fetchSessionPreset,
  isAuthError,
  setDefaultSessionPreset,
  updateSessionPreset,
} from "../api";
import { useBackendCatalog, type BackendCatalog } from "../backends";
import { launchDefaults, type LaunchDefaults } from "../launch/launchDefaults";
import type {
  LaunchTargetSummary,
  MeResponse,
  SessionPreset,
  SessionPresetSummary,
  SessionPresetWriteRequest,
  UsageProviderOption,
} from "../types";

export interface LaunchContext {
  // False until `/api/me` has answered once.
  ready: boolean;
  catalog: BackendCatalog;
  defaults: LaunchDefaults;
  presets: SessionPresetSummary[];
  defaultPresetId: string | null;
  usageProviderOptions: UsageProviderOption[];
  refresh: () => Promise<void>;
  fetchPreset: (presetId: string) => Promise<SessionPreset>;
  // Resolves to the new preset's id when one is created.
  savePreset: (payload: SessionPresetWriteRequest, presetId: string | null) => Promise<string | null>;
  setDefaultPreset: (presetId: string) => Promise<void>;
  deletePreset: (presetId: string) => Promise<void>;
}

const NO_PRESETS: SessionPresetSummary[] = [];
const NO_OPTIONS: UsageProviderOption[] = [];

// What launching needs from `/api/me`: the agent catalog, host defaults, saved
// presets and usage providers, with the launch defaults for the active target.
// Launch targets and their connect state come from the caller's own feed.
// `onAuthFailure` must be stable.
export function useLaunchContext(
  host: string,
  token: string,
  targets: LaunchTargetSummary[],
  activeTargetId: string,
  onAuthFailure: () => void,
): LaunchContext {
  const [me, setMe] = useState<MeResponse | null>(null);

  const refresh = useCallback(async () => {
    try {
      setMe(await fetchMe(host, token));
    } catch (error) {
      if (isAuthError(error)) onAuthFailure();
    }
  }, [host, token, onAuthFailure]);

  useEffect(() => {
    if (!host || !token) return;
    let active = true;
    fetchMe(host, token)
      .then((response) => {
        if (active) setMe(response);
      })
      .catch((error) => {
        if (active && isAuthError(error)) onAuthFailure();
      });
    return () => {
      active = false;
      setMe(null);
    };
  }, [host, token, onAuthFailure]);

  // Fed the same response, so the catalog and the presets arrive together.
  const catalog = useBackendCatalog(host, token, me);
  const activeTarget = targets.find((target) => target.id === activeTargetId) ?? null;
  // Keyed on what the defaults read, so a target's connect state changing keeps
  // the same object and a filled form is not reset.
  const targetKey = JSON.stringify(activeTarget ? { ...activeTarget, connected: undefined } : null);
  const descriptors = me?.backends;
  const hostDefaultBackend = me?.default_backend ?? "";
  const hostDefaultCwd = me?.default_cwd ?? "";
  const defaults = useMemo(
    () =>
      launchDefaults({
        registeredBackends: catalog.ids(),
        descriptors: descriptors ?? [],
        hostDefaultBackend,
        hostDefaultCwd,
        activeTarget: JSON.parse(targetKey) as LaunchTargetSummary | null,
        catalog,
      }),
    [catalog, descriptors, hostDefaultBackend, hostDefaultCwd, targetKey],
  );

  const fetchPreset = useCallback((presetId: string) => fetchSessionPreset(host, token, presetId), [host, token]);
  const savePreset = useCallback(
    async (payload: SessionPresetWriteRequest, presetId: string | null) => {
      let createdId: string | null = null;
      if (presetId) {
        await updateSessionPreset(host, token, presetId, payload);
      } else {
        createdId = (await createSessionPreset(host, token, payload)).id;
      }
      await refresh();
      return createdId;
    },
    [host, token, refresh],
  );
  const setDefaultPreset = useCallback(
    async (presetId: string) => {
      await setDefaultSessionPreset(host, token, presetId);
      await refresh();
    },
    [host, token, refresh],
  );
  const deletePreset = useCallback(
    async (presetId: string) => {
      await deleteSessionPreset(host, token, presetId);
      await refresh();
    },
    [host, token, refresh],
  );

  return {
    ready: me !== null,
    catalog,
    defaults,
    presets: me?.session_presets ?? NO_PRESETS,
    defaultPresetId: me?.default_preset_id ?? null,
    usageProviderOptions: me?.usage_provider_options ?? NO_OPTIONS,
    refresh,
    fetchPreset,
    savePreset,
    setDefaultPreset,
    deletePreset,
  };
}
