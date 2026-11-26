"use client";

import { useId, type ReactNode } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { transportPresentation } from "@waypoint/web-core/backends";
import type { LaunchForm } from "@waypoint/web-core/hooks/useLaunchForm";
import { advancedOverrides } from "@waypoint/web-core/launch/form";
import { defaultModelChoiceLabel, effortChoices, modelChoices } from "@waypoint/web-core/launch/models";
import { effortLabel } from "@waypoint/web-core/modelDisplay";
import type { Backend } from "@waypoint/web-core/types";
import { selectUsageSource, usageSourceChoices } from "@waypoint/web-core/usageLimitSource";

import { ChoiceField } from "@/components/ui/Choice";
import { Field, TextInput } from "@/components/ui/Field";
import { usePointerUI } from "@/lib/layout";

import { Disclosure, EnvField, LinesField } from "./AdvancedFields";
import { FolderField } from "./FolderField";
import { agentOptions, transportOptions } from "./options";
import styles from "./LaunchFields.module.css";

interface LaunchFieldsProps {
  form: LaunchForm;
  catalog: BackendCatalog;
  agents: Backend[];
  host: string;
  token: string;
  launchTargetId: string | null;
  recentCwds: string[];
  cwdError: string | null;
  busy: boolean;
  // The Preset row, where the presets have no column of their own.
  presetRow?: ReactNode;
  // Mode-specific fields placed before Advanced, such as the schedule's timing.
  children?: ReactNode;
}

// The fields New and Schedule share.
export function LaunchFields({
  form,
  catalog,
  agents,
  host,
  token,
  launchTargetId,
  recentCwds,
  cwdError,
  busy,
  presetRow,
  children,
}: LaunchFieldsProps) {
  const pointer = usePointerUI();
  const id = useId();
  const { caps } = form;
  const modelOptions = modelChoices(form.modelInfo?.models.filter((model) => !model.hidden) ?? [], form.model);

  const title = pointer ? (
    <Field label="Title" htmlFor={`${id}-title`}>
      <TextInput id={`${id}-title`} value={form.title} placeholder="Untitled" onChange={(event) => form.setTitle(event.target.value)} />
    </Field>
  ) : (
    <label className={styles.titleRow}>
      <span>Title</span>
      <input value={form.title} placeholder="Untitled" onChange={(event) => form.setTitle(event.target.value)} />
    </label>
  );

  return (
    <div className={styles.fields}>
      {presetRow}
      <div className={pointer ? styles.grid : styles.rows}>
        <ChoiceField label="Agent" value={form.backend} options={agentOptions(agents, catalog)} onChange={form.changeBackend} disabled={busy} />
        {caps.showInterface ? (
          <ChoiceField
            label="Interface"
            value={form.transport}
            hint={transportPresentation(form.transport, catalog).description}
            options={transportOptions(caps.transports, catalog)}
            onChange={form.setTransport}
            disabled={busy}
          />
        ) : null}
        {caps.showAccount ? (
          <ChoiceField
            label="Account"
            value={form.accountProfileId}
            options={[
              { value: "", label: "Default" },
              ...caps.accountProfiles.map((profile) => ({ value: profile.id, label: profile.label })),
            ]}
            onChange={form.setAccountProfileId}
            disabled={busy}
          />
        ) : null}
        {pointer ? title : null}
        <FolderField
          host={host}
          token={token}
          value={form.cwd}
          onChange={form.setCwd}
          launchTargetId={launchTargetId}
          recentCwds={recentCwds}
          error={cwdError}
          disabled={busy}
        />
        {pointer ? null : title}
      </div>

      {caps.showPermission || caps.modelSupported || caps.showEffort ? (
        <section>
          <h3 className={styles.heading}>Tuning</h3>
          <div className={pointer ? styles.grid3 : styles.rows}>
            {caps.showPermission ? (
              <ChoiceField
                label="Permission mode"
                value={form.permissionMode}
                options={caps.permissionOptions.map((mode) => ({ value: mode.id, label: mode.label }))}
                onChange={form.setPermissionMode}
                disabled={busy}
              />
            ) : null}
            {caps.modelSupported ? (
              <ChoiceField
                label="Model"
                value={form.model}
                options={[
                  { value: "", label: defaultModelChoiceLabel(form.modelInfo?.default_model_label) },
                  ...modelOptions.map((model) => ({ value: model.id, label: model.label, detail: model.description ?? undefined })),
                ]}
                onChange={form.setModel}
                disabled={busy}
              />
            ) : null}
            {caps.showEffort ? (
              <ChoiceField
                label="Effort"
                value={form.effort}
                options={[
                  { value: "", label: "Default" },
                  ...effortChoices(caps.effortOptions, form.effort).map((level) => ({ value: level, label: effortLabel(level) })),
                ]}
                onChange={form.setEffort}
                disabled={busy}
              />
            ) : null}
          </div>
        </section>
      ) : null}

      {children}

      <Advanced form={form} busy={busy} />
    </div>
  );
}

// Usage source, environment, CLI arguments and config overrides.
function Advanced({ form, busy }: { form: LaunchForm; busy: boolean }) {
  const pointer = usePointerUI();
  const { caps } = form;
  const showUsage = form.usageProviderOptions.length > 0 || form.usageSelection.source === "usage_provider";
  const usage = usageSourceChoices(form.usageProviderOptions, form.usageSelection);
  const overrides = advancedOverrides(form, caps);

  return (
    <section className={styles.advanced}>
      <Disclosure title="Advanced" summary={overrides.length ? `Sets ${overrides.join(", ")}` : "No overrides"}>
        <div className={pointer ? styles.grid : styles.rows}>
          {showUsage ? (
            <>
              <ChoiceField
                label="Usage limits from"
                value={usage.value}
                options={usage.choices}
                onChange={(choice) => {
                  const next = selectUsageSource(form.usageProviderOptions, choice);
                  if (next) form.setUsageSelection(next);
                }}
                disabled={busy}
              />
              {usage.accounts.length > 0 ? (
                <ChoiceField
                  label="Usage account"
                  value={form.usageSelection.accountKey ?? ""}
                  options={usage.accounts.map((account) => ({ value: account.account_key, label: account.account_label }))}
                  onChange={(accountKey) => form.setUsageSelection({ ...form.usageSelection, accountKey })}
                  disabled={busy}
                />
              ) : null}
            </>
          ) : null}
          <div className={styles.span}>
            <EnvField entries={form.envEntries} onChange={form.setEnvEntries} disabled={busy} />
          </div>
          {caps.supportsCustomArgs ? (
            <LinesField label="CLI arguments" value={form.customArgsText} onChange={form.setCustomArgsText} disabled={busy} />
          ) : null}
          {caps.supportsConfigOverrides ? (
            <LinesField
              label="Config overrides"
              value={form.configOverridesText}
              onChange={form.setConfigOverridesText}
              disabled={busy}
            />
          ) : null}
        </div>
      </Disclosure>
    </section>
  );
}
