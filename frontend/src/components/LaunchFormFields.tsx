"use client";

import { EffortPicker } from "@/components/EffortPicker";
import { LaunchOptionsDetails } from "@/components/LaunchOptions";
import { ModelPicker } from "@/components/ModelPicker";
import { SessionContextFields } from "@/components/SessionContextFields";
import { WorkingDirectoryField } from "@/components/WorkingDirectoryField";
import type { BackendCatalog } from "@waypoint/web-core/backends";
import type { LaunchForm } from "@waypoint/web-core/hooks/useLaunchForm";
import { Backend } from "@waypoint/web-core/types";

interface LaunchFormFieldsProps {
  form: LaunchForm;
  host: string;
  token: string;
  launchTargetId: string | null;
  targetLabel: string | null;
  recentCwds: string[];
  supportedBackends: Backend[];
  catalog: BackendCatalog;
  busy: boolean;
  cwdError?: string | null;
  onClearCwdError?: () => void;
}

// The shared input block for New and Schedule, ordered by the settings
// hierarchy: session context (agent/account profile/interface) selects the
// agent namespace and config root, then session subject (cwd/title), then
// runtime tuning (permission/model/effort) constrained by that context, then
// the collapsible Advanced section. Mono section captions group the fields as
// a flat instrument panel — not nested cards.
export function LaunchFormFields({
  form,
  host,
  token,
  launchTargetId,
  targetLabel,
  recentCwds,
  supportedBackends,
  catalog,
  busy,
  cwdError,
  onClearCwdError,
}: LaunchFormFieldsProps) {
  return (
    <div className="launch-sections">
      <section className="launch-section">
        <span className="launch-section-caption">Session context</span>
        <SessionContextFields
          agents={supportedBackends}
          agent={form.backend}
          onAgentChange={form.changeBackend}
          transport={form.transport}
          onTransportChange={form.setTransport}
          accountProfiles={form.caps.accountProfiles}
          accountProfileId={form.accountProfileId}
          onAccountProfileChange={form.setAccountProfileId}
          targetLabel={targetLabel}
          catalog={catalog}
        />
      </section>

      <section className="launch-section">
        <span className="launch-section-caption">Session</span>
        <WorkingDirectoryField
          host={host}
          token={token}
          cwd={form.cwd}
          onChange={form.setCwd}
          launchTargetId={launchTargetId}
          targetLabel={targetLabel}
          recentCwds={recentCwds}
          error={cwdError}
          onClearError={onClearCwdError}
        />
        <label className="field">
          <span>Title</span>
          <input
            value={form.title}
            onChange={(event) => form.setTitle(event.target.value)}
            placeholder="Optional"
          />
        </label>
      </section>

      <section className="launch-section">
        <span className="launch-section-caption">Tuning</span>
        <div className="field-grid-row">
          {form.caps.permissionOptions.length > 0 ? (
            <label className="field">
              <span>Permission mode</span>
              <select
                value={form.permissionMode}
                onChange={(event) => form.setPermissionMode(event.target.value)}
              >
                {form.caps.permissionOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {form.caps.modelSupported ? (
            <ModelPicker
              models={form.modelInfo?.models ?? []}
              defaultModelLabel={form.modelInfo?.default_model_label ?? null}
              value={form.model}
              onChange={form.setModel}
              disabled={busy}
            />
          ) : null}
          {form.caps.effortSupported ? (
            <EffortPicker
              options={form.caps.effortOptions}
              value={form.effort}
              onChange={form.setEffort}
              disabled={busy}
            />
          ) : null}
        </div>
      </section>

      <LaunchOptionsDetails
        mode="new"
        supportsCustomArgs={form.caps.supportsCustomArgs}
        supportsConfigOverrides={form.caps.supportsConfigOverrides}
        customArgsText={form.customArgsText}
        onCustomArgsChange={form.setCustomArgsText}
        configOverridesText={form.configOverridesText}
        onConfigOverridesChange={form.setConfigOverridesText}
        envEntries={form.envEntries}
        onEnvEntriesChange={form.setEnvEntries}
        usageProviderOptions={form.usageProviderOptions}
        usageSelection={form.usageSelection}
        onUsageSelectionChange={form.setUsageSelection}
        formBusy={busy}
      />
    </div>
  );
}
