"use client";

import type { UsageLimitSource, UsageProviderOption } from "@/lib/types";

// The session's rate-limit readout source: the agent plugin (default) or a
// configured usage-provider account, identified by an opaque account key.
export interface UsageLimitSourceValue {
  source: UsageLimitSource;
  providerId: string | null;
  accountKey: string | null;
}

interface UsageLimitSourceFieldProps {
  // "launch" mirrors the Tuning label.field pattern; "settings" mirrors the
  // settings modal's div.settings-field / select.settings-input pattern.
  variant: "launch" | "settings";
  options: UsageProviderOption[];
  value: UsageLimitSourceValue;
  onChange: (value: UsageLimitSourceValue) => void;
  disabled?: boolean;
}

const PLUGIN_LABEL = "Agent plugin";
const UNAVAILABLE_VALUE = "__unavailable__";

function providerValue(id: string): string {
  return `provider:${id}`;
}

export function UsageLimitSourceField({
  variant,
  options,
  value,
  onChange,
  disabled,
}: UsageLimitSourceFieldProps) {
  const selectedProvider =
    value.source === "usage_provider" && value.providerId
      ? options.find((option) => option.id === value.providerId) ?? null
      : null;
  const selectedAccountExists =
    selectedProvider?.accounts.some(
      (account) => account.account_key === value.accountKey,
    ) ?? false;
  // A persisted provider/account that is no longer published: keep the choice
  // visible (disabled) so the user can see and replace it, never silently
  // dropping it back to the plugin default.
  const unavailable =
    value.source === "usage_provider" && !selectedAccountExists;

  const primaryValue = unavailable
    ? UNAVAILABLE_VALUE
    : selectedProvider
      ? providerValue(selectedProvider.id)
      : "plugin";

  const unavailableLabel = value.providerId
    ? `Unavailable: ${value.providerId} account`
    : "Unavailable account";

  function onPrimaryChange(next: string): void {
    if (next === "plugin") {
      onChange({ source: "plugin", providerId: null, accountKey: null });
      return;
    }
    if (next.startsWith("provider:")) {
      const id = next.slice("provider:".length);
      const provider = options.find((option) => option.id === id);
      if (!provider || provider.accounts.length === 0) return;
      onChange({
        source: "usage_provider",
        providerId: id,
        // Default to the first account; a multi-account provider reveals the
        // account sub-select below for an explicit pick.
        accountKey: provider.accounts[0].account_key,
      });
    }
  }

  const showAccountSelect =
    selectedProvider !== null && selectedProvider.accounts.length > 1;

  const primaryOptions = (
    <>
      <option value="plugin">{PLUGIN_LABEL}</option>
      {options.map((option) => {
        const empty = option.accounts.length === 0;
        const label = empty
          ? `${option.label} (unavailable)`
          : option.accounts.length === 1
            ? `${option.label} — ${option.accounts[0].account_label}`
            : option.label;
        return (
          <option key={option.id} value={providerValue(option.id)} disabled={empty}>
            {label}
          </option>
        );
      })}
      {unavailable ? (
        <option value={UNAVAILABLE_VALUE} disabled>
          {unavailableLabel}
        </option>
      ) : null}
    </>
  );

  if (variant === "settings") {
    return (
      <>
        <div className="settings-field">
          <label className="settings-field-label" htmlFor="settings-usage-source">
            Usage limit source
          </label>
          <select
            id="settings-usage-source"
            className="settings-input"
            value={primaryValue}
            onChange={(event) => onPrimaryChange(event.target.value)}
            disabled={disabled}
          >
            {primaryOptions}
          </select>
        </div>
        {showAccountSelect ? (
          <div className="settings-field">
            <label
              className="settings-field-label"
              htmlFor="settings-usage-account"
            >
              Account
            </label>
            <select
              id="settings-usage-account"
              className="settings-input"
              value={value.accountKey ?? ""}
              onChange={(event) =>
                onChange({
                  source: "usage_provider",
                  providerId: selectedProvider!.id,
                  accountKey: event.target.value,
                })
              }
              disabled={disabled}
            >
              {selectedProvider!.accounts.map((account) => (
                <option key={account.account_key} value={account.account_key}>
                  {account.account_label}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <>
      <label className="field">
        <span>Usage limit source</span>
        <select
          value={primaryValue}
          onChange={(event) => onPrimaryChange(event.target.value)}
          disabled={disabled}
        >
          {primaryOptions}
        </select>
      </label>
      {showAccountSelect ? (
        <label className="field">
          <span>Account</span>
          <select
            value={value.accountKey ?? ""}
            onChange={(event) =>
              onChange({
                source: "usage_provider",
                providerId: selectedProvider!.id,
                accountKey: event.target.value,
              })
            }
            disabled={disabled}
          >
            {selectedProvider!.accounts.map((account) => (
              <option key={account.account_key} value={account.account_key}>
                {account.account_label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </>
  );
}
