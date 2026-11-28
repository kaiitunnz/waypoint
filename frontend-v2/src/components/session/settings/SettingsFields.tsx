"use client";

import { permissionModesFor, transportLabel, type BackendCatalog } from "@waypoint/web-core/backends";
import type { SessionSettingsController } from "@waypoint/web-core/hooks/useSessionSettings";
import { defaultModelChoiceLabel, effortChoices, modelChoices } from "@waypoint/web-core/launch/models";
import { effortLabel } from "@waypoint/web-core/modelDisplay";
import { settingsFieldGates } from "@waypoint/web-core/session/settingsFields";
import type { AssistantSettingsSource, SessionRecord } from "@waypoint/web-core/types";
import { selectUsageSource, usageSourceChoices } from "@waypoint/web-core/usageLimitSource";

import { Disclosure, LinesField } from "@/components/launch/AdvancedFields";
import { ChoiceRow, type ChoiceOption } from "@/components/ui/Choice";
import { TitleField } from "@/components/ui/TitleField";

import { AssistantFields } from "./AssistantFields";
import { SettingsEnv } from "./SettingsEnv";
import styles from "./SettingsFields.module.css";

interface SettingsFieldsProps {
  controller: SessionSettingsController;
  catalog: BackendCatalog;
  session: SessionRecord;
  assistant?: AssistantSettingsSource;
}

// A session's settings in the order the launch form has them, each shown only where the session
// can change it. A change is marked until it is applied.
export function SettingsFields({ controller, catalog, session, assistant }: SettingsFieldsProps) {
  const c = controller;
  const current = c.session ?? session;
  const isAssistant = Boolean(assistant);
  const busy = c.loading || c.applying;
  const permissionModes = permissionModesFor(current.backend, catalog);
  const gates = settingsFieldGates({
    caps: c.caps,
    launchSettings: c.launchSettings,
    transportOptions: c.transportOptions,
    transport: c.transport,
    transportChanged: c.transportChanged,
    models: c.models,
    effortLevels: c.effortLevels,
    model: c.model,
    effort: c.effort,
    usageProviderOptions: c.usageProviderOptions,
    usageSelection: c.usageSelection,
    launchFieldsAvailable: c.launchFieldsAvailable,
    launchFieldsDisabledReason: c.launchFieldsDisabledReason,
    session: c.session,
    openedTransport: session.transport,
    permissionModes,
    isAssistant,
  });

  const withUnset = (options: ChoiceOption[], value: string | null): ChoiceOption[] =>
    value === null ? [{ value: "", label: "Default" }, ...options] : options;
  const usage = usageSourceChoices(c.usageProviderOptions, c.usageSelection);
  const tuning = gates.showPermission || gates.showModel || gates.showEffort;

  return (
    <div className={styles.fields}>
      {isAssistant && assistant ? <AssistantFields controller={c} catalog={catalog} assistant={assistant} session={current} disabled={busy} /> : null}

      <div className={styles.rows}>
        <TitleField value={c.title} onChange={c.setTitle} disabled={busy} staged={c.changed.title} />
        {c.transportSwitchAvailable ? (
          <ChoiceRow
            label="Interface"
            value={c.transport ?? current.transport}
            options={c.transportOptions.map((option) => ({ value: option.id, label: transportLabel(option.id, catalog) ?? option.id }))}
            onChange={c.setTransport}
            disabled={busy}
            staged={c.changed.transport}
          />
        ) : null}
        {gates.showAccountProfile ? (
          <ChoiceRow
            label="Account"
            value={c.accountProfileId ?? ""}
            options={[{ value: "", label: "No profile" }, ...gates.accountProfiles.map((profile) => ({ value: profile.id, label: profile.label }))]}
            onChange={(value) => c.setAccountProfileId(value || null)}
            disabled={busy || c.assistantReplacementStaged}
            staged={c.changed.profile}
          />
        ) : null}
      </div>

      {tuning ? (
        <section>
          <h3 className={styles.heading}>Tuning</h3>
          <div className={styles.rows}>
            {gates.showPermission ? (
              <ChoiceRow
                label="Permission mode"
                value={c.permissionMode ?? ""}
                options={withUnset(
                  permissionModes.map((mode) => ({ value: mode.id, label: mode.label })),
                  c.permissionMode,
                )}
                onChange={(value) => c.setPermissionMode(value || null)}
                disabled={busy}
                staged={c.changed.permission}
              />
            ) : null}
            {gates.showModel ? (
              <ChoiceRow
                label="Model"
                value={c.model ?? ""}
                options={[
                  { value: "", label: defaultModelChoiceLabel(c.defaultModelLabel) },
                  ...modelChoices(c.models, c.model).map((model) => ({ value: model.id, label: model.label, detail: model.description ?? undefined })),
                ]}
                onChange={(value) => c.setModel(value || null)}
                disabled={busy}
                staged={c.changed.model}
              />
            ) : null}
            {gates.showEffort ? (
              <ChoiceRow
                label="Effort"
                value={c.effort ?? ""}
                options={withUnset(
                  effortChoices(gates.effortOptions, c.effort ?? "").map((level) => ({ value: level, label: effortLabel(level) })),
                  c.effort,
                )}
                onChange={(value) => c.setEffort(value || null)}
                disabled={busy}
                staged={c.changed.effort}
              />
            ) : null}
          </div>
        </section>
      ) : null}

      {gates.advancedVisible ? (
        <section className={styles.advanced}>
          <Disclosure title="Advanced" summary={c.changed.usage || c.changed.args || c.changed.config || c.changed.env ? "Changed" : ""}>
            <div className={styles.rows}>
              {gates.usageSourceVisible ? (
                <>
                  <ChoiceRow
                    label="Usage limits from"
                    value={usage.value}
                    options={usage.choices}
                    onChange={(choice) => {
                      const next = selectUsageSource(c.usageProviderOptions, choice);
                      if (next) c.setUsageSelection(next);
                    }}
                    disabled={busy}
                    staged={c.changed.usage}
                  />
                  {usage.accounts.length > 0 ? (
                    <ChoiceRow
                      label="Usage account"
                      value={c.usageSelection.accountKey ?? ""}
                      options={usage.accounts.map((account) => ({ value: account.account_key, label: account.account_label }))}
                      onChange={(accountKey) => c.setUsageSelection({ ...c.usageSelection, accountKey })}
                      disabled={busy}
                    />
                  ) : null}
                </>
              ) : null}
              {c.launchFieldsDisabledReason ? (
                <p className={styles.note}>{c.launchFieldsDisabledReason}</p>
              ) : (
                <>
                  {c.launchSettings?.supports_custom_args ? (
                    <LinesField label="CLI arguments" value={c.argsText} onChange={c.setArgsText} disabled={busy} staged={c.changed.args} />
                  ) : null}
                  {c.launchSettings?.supports_config_overrides ? (
                    <LinesField label="Config overrides" value={c.configOverridesText} onChange={c.setConfigOverridesText} disabled={busy} staged={c.changed.config} />
                  ) : null}
                  <SettingsEnv controller={c} disabled={busy} />
                </>
              )}
            </div>
          </Disclosure>
        </section>
      ) : null}
    </div>
  );
}
