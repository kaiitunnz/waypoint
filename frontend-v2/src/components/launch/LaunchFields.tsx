"use client";

import { useId, useState, type ReactNode } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { humaniseBackend, transportPresentation } from "@waypoint/web-core/backends";
import type { LaunchForm } from "@waypoint/web-core/hooks/useLaunchForm";
import { envSummary } from "@waypoint/web-core/launch/env";
import { defaultModelLabel, modelChoices } from "@waypoint/web-core/launch/models";
import { effortLabel } from "@waypoint/web-core/modelDisplay";
import type { Backend } from "@waypoint/web-core/types";
import { selectUsageSource, usageSourceChoices } from "@waypoint/web-core/usageLimitSource";

import { Button } from "@/components/ui/Button";
import { ChoiceField, type ChoiceOption } from "@/components/ui/Choice";
import { Sheet } from "@/components/ui/Dialog";
import { Field, TextArea, TextInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { SettingsRow } from "@/components/ui/Settings";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import { EnvRows } from "./EnvRows";
import { FolderField } from "./FolderField";
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

export function agentOptions(agents: Backend[], catalog: BackendCatalog): ChoiceOption[] {
  return agents.map((id) => ({ value: id, label: humaniseBackend(id, catalog) }));
}

// The fields New and Schedule share: what runs and how, where, and its tuning.
// Interface, Account, Permission mode, Model and Effort show only when offered.
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
  const transport = transportPresentation(form.transport, catalog);
  const modelOptions = modelChoices(form.modelInfo?.models.filter((model) => !model.hidden) ?? [], form.model);
  const effortOptions = form.effort && !caps.effortOptions.includes(form.effort) ? [form.effort, ...caps.effortOptions] : caps.effortOptions;

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
            hint={transport.description}
            options={caps.transports.map((value) => {
              const shown = transportPresentation(value, catalog);
              return { value, label: shown.name, detail: shown.description };
            })}
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
                  { value: "", label: defaultModelLabel(form.modelInfo?.default_model_label) },
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
                options={[{ value: "", label: "Default" }, ...effortOptions.map((level) => ({ value: level, label: effortLabel(level) }))]}
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

function advancedSummary(form: LaunchForm): string {
  const parts: string[] = [];
  if (form.usageSelection.source !== "plugin") parts.push("usage");
  if (form.envEntries.some((entry) => !entry.removed && entry.key.trim())) parts.push("environment");
  if (form.caps.supportsCustomArgs && form.customArgsText.trim()) parts.push("arguments");
  if (form.caps.supportsConfigOverrides && form.configOverridesText.trim()) parts.push("overrides");
  return parts.length ? `Sets ${parts.join(", ")}` : "No overrides";
}

const firstLine = (text: string) => text.split("\n").map((line) => line.trim()).find(Boolean) ?? "";

// Usage source, environment, CLI arguments and config overrides: inline on a
// pointer client, each a row that opens its editor on touch.
function Advanced({ form, busy }: { form: LaunchForm; busy: boolean }) {
  const pointer = usePointerUI();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<"env" | "args" | "overrides" | null>(null);
  const { caps } = form;
  const showUsage = form.usageProviderOptions.length > 0 || form.usageSelection.source === "usage_provider";
  const usage = usageSourceChoices(form.usageProviderOptions, form.usageSelection);

  const usageFields = showUsage ? (
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
  ) : null;

  const env = <EnvRows entries={form.envEntries} onChange={form.setEnvEntries} disabled={busy} />;
  const argsArea = (
    <TextArea
      id={`${id}-args`}
      rows={3}
      spellCheck={false}
      autoCapitalize="none"
      autoCorrect="off"
      mono
      value={form.customArgsText}
      onChange={(event) => form.setCustomArgsText(event.target.value)}
      disabled={busy}
    />
  );
  const overridesArea = (
    <TextArea
      id={`${id}-overrides`}
      rows={3}
      spellCheck={false}
      autoCapitalize="none"
      autoCorrect="off"
      mono
      value={form.configOverridesText}
      onChange={(event) => form.setConfigOverridesText(event.target.value)}
      disabled={busy}
    />
  );

  return (
    <section className={styles.advanced}>
      <button type="button" className={styles.disclosure} aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <span className={styles.disclosureTitle}>Advanced</span>
        {open ? null : <span className={styles.disclosureDetail}>{advancedSummary(form)}</span>}
        <Icon name={open ? "chevron-up" : "chevron-down"} size={18} />
      </button>
      {open ? (
        pointer ? (
          <div className={styles.grid}>
            {usageFields}
            <div className={cx(styles.span, styles.envField)}>
              <span className={styles.fieldLabel}>Environment variables</span>
              {env}
            </div>
            {caps.supportsCustomArgs ? (
              <Field label="CLI arguments" htmlFor={`${id}-args`}>
                {argsArea}
              </Field>
            ) : null}
            {caps.supportsConfigOverrides ? (
              <Field label="Config overrides" htmlFor={`${id}-overrides`}>
                {overridesArea}
              </Field>
            ) : null}
          </div>
        ) : (
          <div className={styles.rows}>
            {usageFields}
            <SettingsRow
              label="Environment"
              detail={envSummary(form.envEntries) || "None"}
              mono
              onPress={() => setEditing("env")}
            />
            {caps.supportsCustomArgs ? (
              <SettingsRow label="CLI arguments" detail={firstLine(form.customArgsText) || "None"} mono onPress={() => setEditing("args")} />
            ) : null}
            {caps.supportsConfigOverrides ? (
              <SettingsRow
                label="Config overrides"
                detail={firstLine(form.configOverridesText) || "None"}
                mono
                onPress={() => setEditing("overrides")}
              />
            ) : null}
          </div>
        )
      ) : null}
      {pointer ? null : (
        <Sheet
          open={editing !== null}
          onClose={() => setEditing(null)}
          title={editing === "env" ? "Environment" : editing === "args" ? "CLI arguments" : "Config overrides"}
          footer={
            <Button variant="primary" block onClick={() => setEditing(null)}>
              Done
            </Button>
          }
        >
          {editing === "env" ? env : editing === "args" ? argsArea : editing === "overrides" ? overridesArea : null}
        </Sheet>
      )}
    </section>
  );
}
