"use client";

import type { UsageProviderOption } from "@waypoint/web-core/types";
import {
  selectUsageSource,
  type UsageLimitSourceValue,
  usageSourceChoices,
} from "@waypoint/web-core/usageLimitSource";

interface UsageLimitSourceFieldProps {
  variant: "launch" | "settings";
  options: UsageProviderOption[];
  value: UsageLimitSourceValue;
  onChange: (value: UsageLimitSourceValue) => void;
  disabled?: boolean;
}

export function UsageLimitSourceField({
  variant,
  options,
  value,
  onChange,
  disabled,
}: UsageLimitSourceFieldProps) {
  const { value: primaryValue, choices, accounts } = usageSourceChoices(options, value);

  function onPrimaryChange(next: string): void {
    const selection = selectUsageSource(options, next);
    if (selection) onChange(selection);
  }

  const showAccountSelect = accounts.length > 0;

  const primaryOptions = choices.map((choice) => (
    <option key={choice.value} value={choice.value} disabled={choice.disabled}>
      {choice.label}
    </option>
  ));

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
              onChange={(event) => onChange({ ...value, accountKey: event.target.value })}
              disabled={disabled}
            >
              {accounts.map((account) => (
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
            onChange={(event) => onChange({ ...value, accountKey: event.target.value })}
            disabled={disabled}
          >
            {accounts.map((account) => (
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
