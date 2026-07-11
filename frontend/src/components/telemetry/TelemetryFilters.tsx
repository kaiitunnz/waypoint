"use client";

import { useId, useState, type KeyboardEvent } from "react";

import {
  RANGE_PRESET_OPTIONS,
  SESSION_SOURCE_OPTIONS,
  TelemetryFiltersState,
  TelemetryRangeState,
} from "@/lib/telemetry";
import { TelemetryParentScope } from "@/lib/types";

interface TelemetryFiltersProps {
  range: TelemetryRangeState;
  filters: TelemetryFiltersState;
  onRangeChange: (range: TelemetryRangeState) => void;
  onFiltersChange: (filters: TelemetryFiltersState) => void;
  backendOptions: { id: string; label: string }[];
  transportOptions: string[];
  effectiveRangeLabel: string | null;
}

function ChipToggle({
  label,
  active,
  onToggle,
}: {
  label: string;
  active: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className={`tm-chip${active ? " is-active" : ""}`}
      aria-pressed={active}
      onClick={onToggle}
    >
      {label}
    </button>
  );
}

function toggleInList(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

function TagField({
  label,
  hint,
  values,
  onChange,
}: {
  label: string;
  hint: string;
  values: string[];
  onChange: (values: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const inputId = useId();

  const commit = () => {
    const value = draft.trim();
    if (!value || values.includes(value)) {
      setDraft("");
      return;
    }
    onChange([...values, value]);
    setDraft("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
    } else if (event.key === "Backspace" && !draft && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  };

  return (
    <div className="tm-filter-field">
      <label className="tm-filter-label" htmlFor={inputId}>
        {label}
      </label>
      <div className="tm-tag-field">
        {values.map((value) => (
          <span key={value} className="tm-tag-chip">
            {value}
            <button
              type="button"
              className="tm-tag-chip-remove"
              onClick={() => onChange(values.filter((item) => item !== value))}
              aria-label={`Remove ${label.toLowerCase()} filter ${value}`}
            >
              ×
            </button>
          </span>
        ))}
        <input
          id={inputId}
          className="tm-tag-input"
          type="text"
          placeholder={hint}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={commit}
        />
      </div>
    </div>
  );
}

const PARENT_SCOPE_OPTIONS: { value: TelemetryParentScope; label: string }[] = [
  { value: "all", label: "All sessions" },
  { value: "top_level", label: "Top-level only" },
  { value: "children", label: "Children only" },
];

export function TelemetryFilters({
  range,
  filters,
  onRangeChange,
  onFiltersChange,
  backendOptions,
  transportOptions,
  effectiveRangeLabel,
}: TelemetryFiltersProps) {
  const [expanded, setExpanded] = useState(false);
  const parentFieldId = useId();

  return (
    <section className="panel tm-filters" aria-label="Telemetry filters">
      <div className="tm-filters-row">
        <div className="tm-range-presets" role="group" aria-label="Date range">
          {RANGE_PRESET_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`tm-chip${range.preset === option.value ? " is-active" : ""}`}
              aria-pressed={range.preset === option.value}
              onClick={() => onRangeChange({ ...range, preset: option.value })}
            >
              {option.label}
            </button>
          ))}
        </div>

        {range.preset === "custom" ? (
          <div className="tm-custom-range">
            <label className="tm-filter-label" htmlFor="tm-range-start">
              From
            </label>
            <input
              id="tm-range-start"
              type="date"
              className="tm-date-input"
              value={range.start}
              max={range.end || undefined}
              onChange={(event) => onRangeChange({ ...range, start: event.target.value })}
            />
            <label className="tm-filter-label" htmlFor="tm-range-end">
              To
            </label>
            <input
              id="tm-range-end"
              type="date"
              className="tm-date-input"
              value={range.end}
              min={range.start || undefined}
              onChange={(event) => onRangeChange({ ...range, end: event.target.value })}
            />
          </div>
        ) : null}

        {effectiveRangeLabel ? (
          <span className="tm-effective-range">{effectiveRangeLabel}</span>
        ) : null}

        <button
          type="button"
          className="tm-filters-toggle"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          {expanded ? "Hide filters" : "More filters"}
          <span aria-hidden="true" className="tm-filters-toggle-chevron">
            {expanded ? "▴" : "▾"}
          </span>
        </button>
      </div>

      {expanded ? (
        <div className="tm-filters-body">
          {backendOptions.length > 0 ? (
            <div className="tm-filter-field">
              <span className="tm-filter-label">Backend</span>
              <div className="tm-chip-row" role="group" aria-label="Filter by backend">
                {backendOptions.map((option) => (
                  <ChipToggle
                    key={option.id}
                    label={option.label}
                    active={filters.backends.includes(option.id)}
                    onToggle={() =>
                      onFiltersChange({
                        ...filters,
                        backends: toggleInList(filters.backends, option.id),
                      })
                    }
                  />
                ))}
              </div>
            </div>
          ) : null}

          <div className="tm-filter-field">
            <span className="tm-filter-label">Source</span>
            <div className="tm-chip-row" role="group" aria-label="Filter by session source">
              {SESSION_SOURCE_OPTIONS.map((option) => (
                <ChipToggle
                  key={option.value}
                  label={option.label}
                  active={filters.sources.includes(option.value)}
                  onToggle={() =>
                    onFiltersChange({
                      ...filters,
                      sources: toggleInList(filters.sources, option.value),
                    })
                  }
                />
              ))}
            </div>
          </div>

          {transportOptions.length > 0 ? (
            <div className="tm-filter-field">
              <span className="tm-filter-label">Transport</span>
              <div className="tm-chip-row" role="group" aria-label="Filter by transport">
                {transportOptions.map((transport) => (
                  <ChipToggle
                    key={transport}
                    label={transport}
                    active={filters.transports.includes(transport)}
                    onToggle={() =>
                      onFiltersChange({
                        ...filters,
                        transports: toggleInList(filters.transports, transport),
                      })
                    }
                  />
                ))}
              </div>
            </div>
          ) : null}

          <TagField
            label="Model"
            hint="type a model id, ↵ to add"
            values={filters.models}
            onChange={(models) => onFiltersChange({ ...filters, models })}
          />
          <TagField
            label="Repo"
            hint="type a repo name, ↵ to add"
            values={filters.repos}
            onChange={(repos) => onFiltersChange({ ...filters, repos })}
          />
          <TagField
            label="Tag"
            hint="key:value, ↵ to add"
            values={filters.tags}
            onChange={(tags) => onFiltersChange({ ...filters, tags })}
          />

          <div className="tm-filter-field">
            <span className="tm-filter-label">Parent / child</span>
            <div className="tm-chip-row" role="group" aria-label="Filter by parent scope">
              {PARENT_SCOPE_OPTIONS.map((option) => (
                <ChipToggle
                  key={option.value}
                  label={option.label}
                  active={filters.parentScope === option.value}
                  onToggle={() =>
                    onFiltersChange({ ...filters, parentScope: option.value })
                  }
                />
              ))}
            </div>
            <div className="tm-parent-row">
              <label className="tm-filter-sublabel" htmlFor={parentFieldId}>
                Parent session id
              </label>
              <input
                id={parentFieldId}
                type="text"
                className="tm-text-input"
                placeholder="session id (optional)"
                value={filters.parentSessionId}
                onChange={(event) =>
                  onFiltersChange({ ...filters, parentSessionId: event.target.value })
                }
              />
              <label className="tm-checkbox-label">
                <input
                  type="checkbox"
                  checked={filters.includeDescendants}
                  onChange={(event) =>
                    onFiltersChange({
                      ...filters,
                      includeDescendants: event.target.checked,
                    })
                  }
                />
                Include descendants
              </label>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
