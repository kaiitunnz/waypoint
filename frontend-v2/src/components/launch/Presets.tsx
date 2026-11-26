"use client";

import { useId, useMemo, useRef, useState, type FormEvent } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import { humaniseBackend } from "@waypoint/web-core/backends";
import {
  deletePresetMessage,
  filterPresets,
  orderPresets,
  presetSubLabel,
  specSummaryChips,
  summarizeSpec,
} from "@waypoint/web-core/launch/presets";
import type {
  AccountProfile,
  SessionPresetSpec,
  SessionPresetSummary,
  SessionPresetWriteRequest,
  UsageProviderOption,
} from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { Field, SearchInput, TextInput } from "@/components/ui/Field";
import { Flyout } from "@/components/ui/Flyout";
import { Icon } from "@/components/ui/Icon";
import { MenuButton, type MenuButtonItem } from "@/components/ui/MenuButton";
import { SettingsRow } from "@/components/ui/Settings";
import { Toggle } from "@/components/ui/Toggle";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import type { PresetControls } from "./usePresetControls";
import styles from "./Presets.module.css";

const NO_PRESET = "No preset";

function PresetName({ preset, catalog }: { preset: SessionPresetSummary; catalog: BackendCatalog }) {
  const sub = presetSubLabel(preset.spec, catalog);
  return (
    <span className={styles.text}>
      <span className={styles.name}>{preset.name}</span>
      {sub ? <span className={styles.sub}>{sub}</span> : null}
    </span>
  );
}

function DefaultMark({ label }: { label?: boolean }) {
  return (
    <span className={styles.defaultMark}>
      {label ? <span>Default</span> : null}
      <span className={styles.fold} aria-hidden="true" />
    </span>
  );
}

function menuItems(preset: SessionPresetSummary, withSaveAs: boolean): MenuButtonItem[] {
  return [
    { id: "update", label: "Update with these settings" },
    ...(withSaveAs ? [{ id: "saveAs", label: "Save as a new preset…" }] : []),
    preset.is_default ? { id: "default", label: "Default preset ✓", disabled: true } : { id: "default", label: "Set as default" },
    { id: "delete", label: "Delete preset", tone: "danger" as const },
  ];
}

function onMenu(controls: PresetControls, preset: SessionPresetSummary, id: string) {
  if (id === "update") controls.update(preset);
  else if (id === "saveAs") controls.openSave();
  else if (id === "default") controls.setDefault(preset);
  else if (id === "delete") controls.askDelete(preset);
}

// The presets column of the launch modal on a wide pointer client.
export function PresetsColumn({ controls, catalog }: { controls: PresetControls; catalog: BackendCatalog }) {
  const ordered = useMemo(
    () => orderPresets(controls.presets, controls.defaultPresetId),
    [controls.presets, controls.defaultPresetId],
  );
  return (
    <aside className={styles.column} aria-label="Presets">
      <h3 className={styles.columnHead}>
        Presets <span className={styles.count}>{controls.presets.length}</span>
      </h3>
      <ul className={styles.columnList}>
        <li>
          <button
            type="button"
            className={cx(styles.columnRow, controls.selectedId === null && styles.selected)}
            aria-current={controls.selectedId === null || undefined}
            onClick={() => controls.select(null)}
          >
            <span className={styles.text}>
              <span className={styles.plain}>{NO_PRESET}</span>
            </span>
          </button>
        </li>
        {ordered.map((preset) => {
          const selected = preset.id === controls.selectedId;
          return (
            <li key={preset.id} className={styles.columnItem}>
              <button
                type="button"
                className={cx(styles.columnRow, selected && styles.selected)}
                aria-current={selected || undefined}
                onClick={() => controls.select(preset.id)}
              >
                <PresetName preset={preset} catalog={catalog} />
              </button>
              {preset.is_default ? <DefaultMark /> : null}
              {selected ? (
                <span className={styles.rowMenu}>
                  <MenuButton
                    label={`${preset.name} actions`}
                    title={preset.name}
                    size="sm"
                    items={menuItems(preset, true)}
                    onSelect={(id) => onMenu(controls, preset, id)}
                  />
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>
      <div className={styles.columnFoot}>
        <Button variant="text" size="sm" disabled={controls.busy} onClick={() => controls.openSave()}>
          <Icon name="plus" size={16} />
          Save as a new preset
        </Button>
      </div>
    </aside>
  );
}

// The Preset row that opens the picker: a sheet on touch, a popover on a narrow pointer client.
export function PresetRow({ controls, catalog }: { controls: PresetControls; catalog: BackendCatalog }) {
  const pointer = usePointerUI();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const name = controls.selected?.name ?? NO_PRESET;
  const close = () => setOpen(false);
  return (
    <>
      {pointer ? (
        <div className={styles.pointerRow}>
          <span className={styles.pointerLabel}>Preset</span>
          <button
            ref={trigger}
            type="button"
            className={styles.pointerTrigger}
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            <span className={controls.selected ? styles.mono : undefined}>{name}</span>
            <Icon name="chevron-down" size={16} />
          </button>
        </div>
      ) : (
        <div className={styles.touchRow}>
          <SettingsRow label="Preset" detail={name} mono={Boolean(controls.selected)} onPress={() => setOpen(true)} />
        </div>
      )}
      <Flyout
        open={open}
        onClose={close}
        triggerRef={trigger}
        title="Presets"
        subtitle={`${controls.presets.length} saved`}
        width={400}
        flush
      >
        <PresetPicker controls={controls} catalog={catalog} onDone={close} />
      </Flyout>
    </>
  );
}

function PresetPicker({
  controls,
  catalog,
  onDone,
}: {
  controls: PresetControls;
  catalog: BackendCatalog;
  onDone: () => void;
}) {
  const [query, setQuery] = useState("");
  const shown = useMemo(
    () => filterPresets(orderPresets(controls.presets, controls.defaultPresetId), query),
    [controls.presets, controls.defaultPresetId, query],
  );
  const choose = (id: string | null) => {
    controls.select(id);
    onDone();
  };
  return (
    <div className={styles.picker}>
      <div className={styles.pickerTop}>
        <SearchInput
          aria-label="Search presets"
          placeholder="Search presets"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button type="button" className={styles.saveCurrent} disabled={controls.busy} onClick={() => controls.openSave()}>
          <Icon name="plus" size={18} />
          Save current settings as a preset
        </button>
      </div>
      <ul className={styles.pickerList}>
        {query.trim() ? null : (
          <li>
            <button
              type="button"
              className={cx(styles.pickerRow, controls.selectedId === null && styles.selected)}
              aria-current={controls.selectedId === null || undefined}
              onClick={() => choose(null)}
            >
              <span className={styles.check}>{controls.selectedId === null ? <Icon name="check" size={18} /> : null}</span>
              <span className={styles.plain}>{NO_PRESET}</span>
            </button>
          </li>
        )}
        {shown.map((preset) => {
          const selected = preset.id === controls.selectedId;
          return (
            <li key={preset.id} className={cx(styles.pickerItem, selected && styles.selected)}>
              <div className={styles.pickerLine}>
                <button
                  type="button"
                  className={styles.pickerRow}
                  aria-current={selected || undefined}
                  onClick={() => choose(preset.id)}
                >
                  <span className={styles.check}>{selected ? <Icon name="check" size={18} /> : null}</span>
                  <PresetName preset={preset} catalog={catalog} />
                </button>
                {preset.is_default ? <DefaultMark label /> : null}
                {selected ? null : (
                  <span className={styles.lineMenu}>
                    <MenuButton
                      label={`${preset.name} actions`}
                      title={preset.name}
                      size="sm"
                      items={menuItems(preset, false)}
                      onSelect={(id) => onMenu(controls, preset, id)}
                    />
                  </span>
                )}
              </div>
              {selected ? (
                <div className={styles.inlineActions}>
                  <Button variant="text" size="sm" disabled={controls.busy} onClick={() => controls.update(preset)}>
                    Update with these settings
                  </Button>
                  {preset.is_default ? null : (
                    <Button variant="text" size="sm" disabled={controls.busy} onClick={() => controls.setDefault(preset)}>
                      Set as default
                    </Button>
                  )}
                  <Button variant="danger" size="sm" disabled={controls.busy} onClick={() => controls.askDelete(preset)}>
                    Delete
                  </Button>
                </div>
              ) : null}
            </li>
          );
        })}
        {shown.length === 0 && query.trim() ? <li className={styles.empty}>No presets match.</li> : null}
      </ul>
    </div>
  );
}

export function PresetDialogs({
  controls,
  spec,
  catalog,
  profiles,
  usageProviders,
}: {
  controls: PresetControls;
  spec: SessionPresetSpec;
  catalog: BackendCatalog;
  profiles: AccountProfile[];
  usageProviders: UsageProviderOption[];
}) {
  return (
    <>
      {controls.saving ? (
        <PresetSaveDialog
          spec={spec}
          catalog={catalog}
          profiles={profiles}
          usageProviders={usageProviders}
          busy={controls.busy}
          onClose={controls.closeSave}
          onSave={controls.save}
        />
      ) : null}
      <ConfirmDialog
        open={controls.pendingDelete !== null}
        title="Delete preset"
        detail={controls.pendingDelete ? deletePresetMessage(controls.pendingDelete) : ""}
        confirmLabel="Delete"
        onConfirm={controls.confirmDelete}
        onClose={controls.cancelDelete}
      />
    </>
  );
}

function PresetSaveDialog({
  spec,
  catalog,
  profiles,
  usageProviders,
  busy,
  onClose,
  onSave,
}: {
  spec: SessionPresetSpec;
  catalog: BackendCatalog;
  profiles: AccountProfile[];
  usageProviders: UsageProviderOption[];
  busy: boolean;
  onClose: () => void;
  onSave: (payload: SessionPresetWriteRequest) => Promise<void>;
}) {
  const id = useId();
  const pointer = usePointerUI();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [asDefault, setAsDefault] = useState(false);
  const chips = specSummaryChips(summarizeSpec(spec), profiles, usageProviders);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || busy) return;
    void onSave({ name: name.trim(), description: description.trim() || null, spec, is_default: asDefault });
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title="New preset"
      footer={
        <>
          {pointer ? (
            <Button variant="text" onClick={onClose}>
              Cancel
            </Button>
          ) : null}
          <Button variant="primary" type="submit" form={`${id}-form`} block={!pointer} busy={busy} disabled={!name.trim()}>
            Save preset
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={styles.saveForm} onSubmit={submit}>
        <Field label="Name" htmlFor={`${id}-name`}>
          <TextInput
            id={`${id}-name`}
            mono
            data-autofocus=""
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field label="Description" htmlFor={`${id}-description`}>
          <TextInput id={`${id}-description`} value={description} onChange={(event) => setDescription(event.target.value)} />
        </Field>
        <div className={styles.captures}>
          <span className={styles.capturesLabel}>Captures</span>
          <p className={styles.chips}>
            {spec.backend ? <span className={styles.chip}>{humaniseBackend(spec.backend, catalog)}</span> : null}
            {chips.map((chip, index) => (
              <span
                key={`${index}-${chip.text}`}
                className={cx(styles.chip, styles.machine, chip.unavailable && styles.unavailable)}
                title={chip.title}
              >
                {chip.text}
              </span>
            ))}
          </p>
        </div>
        <label className={styles.toggleRow}>
          <span>Make this the default preset</span>
          <Toggle label="Make this the default preset" checked={asDefault} onChange={setAsDefault} />
        </label>
      </form>
    </Dialog>
  );
}
