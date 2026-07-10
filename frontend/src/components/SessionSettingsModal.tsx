"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { AssistantControls } from "@/components/SessionDetail";
import {
  agentTransports,
  defaultTransportFor,
  humaniseBackend,
  launchableAgents,
  permissionModesFor,
  transportLabel,
  useBackendCatalog,
} from "@/lib/backends";
import { trapTabFocus } from "@/lib/keyboard";
import type { Backend, SessionRecord, SessionTransport } from "@/lib/types";
import {
  useSessionSettings,
  type AssistantSettingsControls,
} from "@/lib/useSessionSettings";

export interface AssistantThreadOption {
  id: string;
  title: string;
  updatedAt: string | null;
  preview: string | null;
}

interface SessionSettingsModalProps {
  host: string;
  token: string;
  session: SessionRecord;
  onClose: () => void;
  onApplied?: (session: SessionRecord) => void;
  onAuthFailure?: () => void;
  isAssistant?: boolean;
  assistant?: AssistantControls;
}

function useIsMobile(): boolean {
  const [isMobile] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(max-width: 600px)").matches,
  );
  return isMobile;
}

export function SessionSettingsModal({
  host,
  token,
  session,
  onClose,
  onApplied,
  onAuthFailure,
  isAssistant,
  assistant,
}: SessionSettingsModalProps) {
  const catalog = useBackendCatalog(host || null, token || null, null);
  const modalRef = useRef<HTMLDivElement>(null);
  const isMobile = useIsMobile();
  const [mobileVV, setMobileVV] = useState<{
    height: number;
    width: number;
    offsetTop: number;
    offsetLeft: number;
  } | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [threads, setThreads] = useState<AssistantThreadOption[]>([]);
  const [threadsLoaded, setThreadsLoaded] = useState(false);

  const assistantControls = useMemo<AssistantSettingsControls | undefined>(() => {
    if (!isAssistant || !assistant) return undefined;
    return {
      onSwitchBackend: async (backend, transport, profileId) => {
        await assistant.onSwitchBackend(
          backend,
          transport as SessionTransport,
          profileId,
        );
      },
      onAttachThread: async (backend, threadId, profileId) => {
        await assistant.onAttachThread(backend, threadId, profileId);
      },
    };
  }, [isAssistant, assistant]);

  const controller = useSessionSettings({
    host,
    token,
    open: true,
    session,
    catalog,
    onApplied,
    onAuthFailure,
    isAssistant,
    assistantControls,
  });

  const {
    loading,
    loadError,
    applying,
    applyError,
    partialSuccess,
    session: current,
    launchSettings,
    models,
    defaultModelLabel,
    title,
    permissionMode,
    model,
    effort,
    accountProfileId,
    argsText,
    configOverridesText,
    existingEnv,
    newEnv,
    assistantBackend,
    assistantTransport,
    assistantThreadId,
    assistantReplacementStaged,
    plan,
    applyDisabled,
    launchFieldsAvailable,
    launchFieldsDisabledReason,
    hiddenEnvKeys,
    setTitle,
    setPermissionMode,
    setModel,
    setEffort,
    setAccountProfileId,
    setArgsText,
    setConfigOverridesText,
    setExistingEnvOp,
    setExistingEnvValue,
    addNewEnv,
    updateNewEnv,
    removeNewEnv,
    setAssistantBackend,
    setAssistantTransport,
    setAssistantThreadId,
    apply,
  } = controller;

  const busy = loading || applying;

  const handleKeyDown = useCallback(
    (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && !busy) {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "Tab") {
        trapTabFocus(e, modalRef.current, { preventWhenEmpty: true });
      }
    },
    [busy, onClose],
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown, { capture: true });
    return () =>
      window.removeEventListener("keydown", handleKeyDown, { capture: true });
  }, [handleKeyDown]);

  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = originalOverflow;
    };
  }, []);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    // Move focus into the dialog on open so the Tab trap engages immediately
    // and screen readers announce it; the trigger has usually unmounted.
    modalRef.current?.focus();
    return () => {
      if (previous && typeof previous.focus === "function") previous.focus();
    };
  }, []);

  // Invalidate the cached assistant thread list when the staged agent or
  // account profile changes, so the Resume-thread dropdown can't offer (and
  // attach) a thread that belongs to a different agent/profile.
  useEffect(() => {
    setThreads([]);
    setThreadsLoaded(false);
  }, [assistantBackend, accountProfileId]);

  useEffect(() => {
    if (!isMobile) return;
    const vv = window.visualViewport;
    if (!vv) {
      setMobileVV({
        height: window.innerHeight,
        width: window.innerWidth,
        offsetTop: 0,
        offsetLeft: 0,
      });
      return;
    }
    const update = () =>
      setMobileVV({
        height: vv.height,
        width: vv.width,
        offsetTop: vv.offsetTop,
        offsetLeft: vv.offsetLeft,
      });
    update();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  }, [isMobile]);

  // ── derived option lists ────────────────────────────────────────────────
  const backend = current?.backend ?? session.backend;
  const permissionModes = permissionModesFor(backend, catalog);
  const effortOptions = useMemo(() => {
    const selectedModel = models.find((m) => m.id === (model ?? ""));
    if (selectedModel) return selectedModel.supported_efforts ?? [];
    return Array.from(
      new Set(models.flatMap((m) => m.supported_efforts ?? [])),
    );
  }, [models, model]);
  const accountProfiles = launchSettings?.account_profiles ?? [];

  const showPermission = permissionModes.length > 0;
  const showModel = models.length > 0 || model !== null;
  // Mirror the launch panel's ModelPicker: surface a pre-existing custom model
  // (from an older session/schedule) that isn't in the discovered list.
  const modelEntries =
    model && !models.some((m) => m.id === model)
      ? [{ id: model, label: `Custom · ${model}` }, ...models]
      : models;
  const showEffort = effortOptions.length > 0 || effort !== null;
  const showAccountProfile =
    accountProfiles.length > 0 &&
    Boolean(launchSettings?.supports_account_profile_with_restart);

  const assistantAgents = isAssistant
    ? launchableAgents(assistant?.backends.map((b) => b.id) ?? [], catalog)
    : [];
  const assistantTransportOptions =
    isAssistant && assistantBackend
      ? agentTransports(assistantBackend, catalog)
      : [];

  const loadThreads = useCallback(async () => {
    if (!assistant || !assistantBackend) return;
    setThreadsLoaded(true);
    try {
      const list = await assistant.listThreads(
        assistantBackend,
        accountProfileId,
      );
      setThreads(list as AssistantThreadOption[]);
    } catch {
      setThreads([]);
    }
  }, [assistant, assistantBackend, accountProfileId]);

  const contextLine = useMemo(() => {
    const parts = [
      humaniseBackend(backend, catalog),
      transportLabel(current?.transport ?? session.transport, catalog) ??
        (current?.transport ?? session.transport),
      current?.launch_target_id || "Local",
      current?.cwd ?? session.cwd,
    ];
    return parts.filter(Boolean).join(" · ");
  }, [backend, catalog, current, session]);

  const onApply = useCallback(async () => {
    const ok = await apply();
    if (ok) onClose();
  }, [apply, onClose]);

  const advancedVisible =
    !isAssistant &&
    (launchSettings?.supports_custom_args ||
      launchSettings?.supports_config_overrides ||
      launchFieldsAvailable ||
      launchFieldsDisabledReason !== null);

  const content = (
    <div
      className="settings-modal-backdrop"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Session settings"
        tabIndex={-1}
        ref={modalRef}
        style={
          isMobile && mobileVV !== null
            ? {
                position: "fixed",
                width: `${mobileVV.width * 0.92}px`,
                height: `${mobileVV.height * 0.82}px`,
                maxWidth: `${mobileVV.width * 0.92}px`,
                maxHeight: `${mobileVV.height * 0.82}px`,
                top: `${mobileVV.offsetTop + mobileVV.height * 0.09}px`,
                left: `${mobileVV.offsetLeft + mobileVV.width * 0.04}px`,
              }
            : undefined
        }
      >
        <header className="settings-modal-header">
          <h2 className="settings-modal-title">Session settings</h2>
          <p className="settings-modal-context">{contextLine}</p>
        </header>

        <div className="settings-modal-body">
          {loading ? (
            <p className="settings-modal-note">Loading settings…</p>
          ) : loadError ? (
            <p className="settings-modal-error">{loadError}</p>
          ) : (
            <>
              {/* Assistant agent / interface / resume */}
              {isAssistant && assistant ? (
                <section className="settings-group">
                  <div className="settings-group-caption">Assistant</div>
                  <div className="settings-field">
                    <label className="settings-field-label" htmlFor="settings-agent">
                      Agent
                    </label>
                    <select
                      id="settings-agent"
                      className="settings-input"
                      value={assistantBackend ?? backend}
                      onChange={(e) =>
                        setAssistantBackend(e.target.value as Backend)
                      }
                      disabled={busy}
                    >
                      {assistantAgents.map((id) => (
                        <option key={id} value={id}>
                          {humaniseBackend(id, catalog)}
                        </option>
                      ))}
                    </select>
                  </div>
                  {assistantTransportOptions.length > 1 ? (
                    <div className="settings-field">
                      <label
                        className="settings-field-label"
                        htmlFor="settings-interface"
                      >
                        Interface
                      </label>
                      <select
                        id="settings-interface"
                        className="settings-input"
                        value={
                          assistantTransport ??
                          defaultTransportFor(
                            assistantBackend ?? backend,
                            catalog,
                          ) ??
                          ""
                        }
                        onChange={(e) => setAssistantTransport(e.target.value)}
                        disabled={busy}
                      >
                        {assistantTransportOptions.map((t) => (
                          <option key={t} value={t}>
                            {transportLabel(t, catalog) ?? t}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : null}
                  <div className="settings-field">
                    <label
                      className="settings-field-label"
                      htmlFor="settings-thread"
                    >
                      Resume thread
                    </label>
                    <select
                      id="settings-thread"
                      className="settings-input"
                      value={assistantThreadId ?? ""}
                      onFocus={() => {
                        if (!threadsLoaded) void loadThreads();
                      }}
                      onChange={(e) =>
                        setAssistantThreadId(e.target.value || null)
                      }
                      disabled={busy}
                    >
                      <option value="">New conversation</option>
                      {threads.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.title}
                        </option>
                      ))}
                    </select>
                  </div>
                </section>
              ) : null}

              {/* Account profile — kept with the context group (agent /
                  interface / profile) above Session and Tuning, mirroring the
                  launch panel's field hierarchy. */}
              {showAccountProfile ? (
                <section className="settings-group">
                  <div className="settings-group-caption">Account profile</div>
                  <div className="settings-field">
                    <label
                      className="settings-field-label"
                      htmlFor="settings-profile"
                    >
                      Profile
                    </label>
                    <select
                      id="settings-profile"
                      className="settings-input"
                      value={accountProfileId ?? ""}
                      onChange={(e) =>
                        setAccountProfileId(e.target.value || null)
                      }
                      disabled={busy || assistantReplacementStaged}
                    >
                      {/* A session may currently run under no profile; show a
                          disabled placeholder so the value isn't orphaned.
                          Clearing a profile to none is out of scope, so it
                          can't be re-selected once a real profile is chosen. */}
                      {accountProfileId === null ? (
                        <option value="" disabled>
                          No profile
                        </option>
                      ) : null}
                      {accountProfiles.map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {profile.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </section>
              ) : null}

              {/* Title */}
              <section className="settings-field">
                <label className="settings-field-label" htmlFor="settings-title">
                  Title
                </label>
                <input
                  id="settings-title"
                  className="settings-input"
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={busy}
                />
              </section>

              {/* Tuning */}
              {showPermission || showModel || showEffort ? (
                <section className="settings-group">
                  <div className="settings-group-caption">Tuning</div>
                  {showPermission ? (
                    <div className="settings-field">
                      <label
                        className="settings-field-label"
                        htmlFor="settings-permission"
                      >
                        Permission mode
                      </label>
                      <select
                        id="settings-permission"
                        className="settings-input"
                        value={permissionMode ?? ""}
                        onChange={(e) =>
                          setPermissionMode(e.target.value || null)
                        }
                        disabled={busy}
                      >
                        {permissionModes.map((mode) => (
                          <option key={mode.id} value={mode.id}>
                            {mode.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : null}
                  {showModel ? (
                    <div className="settings-field">
                      <label className="settings-field-label" htmlFor="settings-model">
                        Model
                      </label>
                      {/* Same select-style picker as the launch panel's
                          ModelPicker: a Default option, the discovered models,
                          and a passthrough for a pre-existing custom value. */}
                      <select
                        id="settings-model"
                        className="settings-input"
                        value={model ?? ""}
                        onChange={(e) => setModel(e.target.value || null)}
                        disabled={busy}
                      >
                        <option value="">
                          {defaultModelLabel
                            ? `Default (${defaultModelLabel})`
                            : "Default"}
                        </option>
                        {modelEntries.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : null}
                  {showEffort ? (
                    <div className="settings-field">
                      <label
                        className="settings-field-label"
                        htmlFor="settings-effort"
                      >
                        Reasoning effort
                      </label>
                      <select
                        id="settings-effort"
                        className="settings-input"
                        value={effort ?? ""}
                        onChange={(e) => setEffort(e.target.value || null)}
                        disabled={busy}
                      >
                        {effort !== null &&
                        !effortOptions.includes(effort) ? (
                          <option value={effort}>{effort}</option>
                        ) : null}
                        {effortOptions.map((level) => (
                          <option key={level} value={level}>
                            {level}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : null}
                </section>
              ) : null}

              {/* Advanced launch settings */}
              {advancedVisible ? (
                <section className="settings-group">
                  <button
                    type="button"
                    className="settings-advanced-toggle"
                    aria-expanded={advancedOpen}
                    onClick={() => setAdvancedOpen((v) => !v)}
                  >
                    <span>Advanced</span>
                    <span className="settings-advanced-caret">
                      {advancedOpen ? "▾" : "▸"}
                    </span>
                  </button>
                  {advancedOpen ? (
                    launchFieldsDisabledReason ? (
                      <p className="settings-modal-note">
                        {launchFieldsDisabledReason}
                      </p>
                    ) : (
                      <div className="settings-advanced-body">
                        {launchSettings?.supports_custom_args ? (
                          <div className="settings-field">
                            <label
                              className="settings-field-label"
                              htmlFor="settings-args"
                            >
                              Custom CLI args
                            </label>
                            <textarea
                              id="settings-args"
                              className="settings-input settings-textarea"
                              value={argsText}
                              onChange={(e) => setArgsText(e.target.value)}
                              placeholder="One argument per line"
                              disabled={busy}
                              rows={3}
                            />
                          </div>
                        ) : null}
                        {launchSettings?.supports_config_overrides ? (
                          <div className="settings-field">
                            <label
                              className="settings-field-label"
                              htmlFor="settings-config"
                            >
                              Config overrides
                            </label>
                            <textarea
                              id="settings-config"
                              className="settings-input settings-textarea"
                              value={configOverridesText}
                              onChange={(e) =>
                                setConfigOverridesText(e.target.value)
                              }
                              placeholder={'key="value" per line'}
                              disabled={busy}
                              rows={3}
                            />
                          </div>
                        ) : null}
                        <div className="settings-field">
                          <div className="settings-field-label">
                            Environment variables
                          </div>
                          <div className="settings-env-list">
                            {existingEnv
                              .filter((e) => !hiddenEnvKeys.includes(e.key))
                              .map((entry) => (
                                <div className="settings-env-row" key={entry.key}>
                                  <span
                                    className="settings-env-key"
                                    title={entry.key}
                                  >
                                    {entry.key}
                                  </span>
                                  <select
                                    className="settings-input settings-env-op"
                                    value={entry.op}
                                    onChange={(e) =>
                                      setExistingEnvOp(
                                        entry.key,
                                        e.target
                                          .value as typeof entry.op,
                                      )
                                    }
                                    disabled={busy}
                                    aria-label={`${entry.key} action`}
                                  >
                                    <option value="keep">Keep</option>
                                    <option value="replace">Replace</option>
                                    <option value="remove">Remove</option>
                                  </select>
                                  {entry.op === "replace" ? (
                                    <input
                                      className="settings-input settings-env-value"
                                      type="password"
                                      value={entry.value}
                                      onChange={(e) =>
                                        setExistingEnvValue(
                                          entry.key,
                                          e.target.value,
                                        )
                                      }
                                      placeholder="New value"
                                      disabled={busy}
                                      aria-label={`${entry.key} new value`}
                                    />
                                  ) : (
                                    <span className="settings-env-redacted">
                                      ••••••
                                    </span>
                                  )}
                                </div>
                              ))}
                            {newEnv.map((entry) => (
                              <div className="settings-env-row" key={entry.id}>
                                <input
                                  className="settings-input settings-env-key-input"
                                  type="text"
                                  value={entry.key}
                                  onChange={(e) =>
                                    updateNewEnv(entry.id, {
                                      key: e.target.value,
                                    })
                                  }
                                  placeholder="KEY"
                                  disabled={busy}
                                  aria-label="New variable name"
                                />
                                <input
                                  className="settings-input settings-env-value"
                                  type="password"
                                  value={entry.value}
                                  onChange={(e) =>
                                    updateNewEnv(entry.id, {
                                      value: e.target.value,
                                    })
                                  }
                                  placeholder="value"
                                  disabled={busy}
                                  aria-label="New variable value"
                                />
                                <button
                                  type="button"
                                  className="settings-env-remove"
                                  onClick={() => removeNewEnv(entry.id)}
                                  disabled={busy}
                                  aria-label="Remove variable"
                                >
                                  ✕
                                </button>
                              </div>
                            ))}
                          </div>
                          <button
                            type="button"
                            className="settings-env-add"
                            onClick={addNewEnv}
                            disabled={busy}
                          >
                            + Add variable
                          </button>
                        </div>
                      </div>
                    )
                  ) : null}
                </section>
              ) : null}
            </>
          )}
        </div>

        {plan.warnings.length > 0 && !loading ? (
          <div className="settings-modal-plan" role="status">
            {plan.warnings.map((warning, i) => (
              <p key={i}>{warning}</p>
            ))}
          </div>
        ) : null}

        {applyError ? (
          <div className="settings-modal-plan settings-modal-plan-error">
            <p>
              {partialSuccess
                ? "Some changes were applied before this failed: "
                : ""}
              {applyError}
            </p>
          </div>
        ) : null}

        <footer className="settings-modal-footer">
          <button
            type="button"
            className="settings-btn"
            onClick={onClose}
            disabled={applying}
          >
            Cancel
          </button>
          <button
            type="button"
            className="settings-btn settings-btn-primary"
            onClick={onApply}
            disabled={applyDisabled}
          >
            {applying ? "Applying…" : "Apply"}
          </button>
        </footer>
      </div>
    </div>
  );

  return typeof document !== "undefined"
    ? createPortal(content, document.body)
    : null;
}
