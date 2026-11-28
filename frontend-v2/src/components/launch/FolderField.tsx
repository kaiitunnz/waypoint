"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { useFolderSuggestions } from "@waypoint/web-core/hooks/useFolderSuggestions";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { PathLabel } from "@/components/session/cards/PathLabel";

import { Button } from "@/components/ui/Button";
import { Sheet } from "@/components/ui/Dialog";
import { Field, TextInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { SettingsRow } from "@/components/ui/Settings";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import styles from "./FolderField.module.css";

interface FolderFieldProps {
  host: string;
  token: string;
  value: string;
  onChange: (cwd: string) => void;
  launchTargetId: string | null;
  recentCwds: string[];
  // The folder the server said does not exist.
  error: string | null;
  disabled?: boolean;
}

function FolderPath({ path }: { path: string }) {
  return <PathLabel {...splitPath(path)} full={path} />;
}

// The working directory: recent folders, then the server's completions for what is typed.
export function FolderField(props: FolderFieldProps) {
  const pointer = usePointerUI();
  return pointer ? <FolderCombobox {...props} /> : <FolderRow {...props} />;
}

function ErrorLine({ id, path }: { id?: string; path: string }) {
  return (
    <span className={styles.error} id={id} role="alert">
      Folder not found: <code>{path}</code>
    </span>
  );
}

function FolderCombobox({ host, token, value, onChange, launchTargetId, recentCwds, error, disabled }: FolderFieldProps) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const options = useFolderSuggestions(host, token, value, launchTargetId, recentCwds);
  const shown = open && options.length > 0;

  // A rejected folder takes the focus without opening the list over its error.
  const quietFocus = useRef(false);
  useEffect(() => {
    if (!error) return;
    quietFocus.current = true;
    input.current?.focus();
  }, [error]);

  function pick(option: string) {
    onChange(option);
    setActive(-1);
    // A folder ending in "/" invites its children; the list stays open for them.
    setOpen(option.endsWith("/"));
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (options.length === 0) return;
      event.preventDefault();
      setOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => (current + step + options.length) % options.length);
    } else if (event.key === "Enter" && shown && active >= 0) {
      event.preventDefault();
      pick(options[active]);
    } else if (event.key === "Escape" && shown) {
      // Closes the list, not the dialog.
      event.preventDefault();
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      setOpen(false);
    }
  }

  return (
    <Field label="Folder" htmlFor={id} className={styles.field}>
      <span className={styles.wrap}>
        <TextInput
          autoComplete="off"
          ref={input}
          id={id}
          mono
          tail
          role="combobox"
          aria-expanded={shown}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          aria-activedescendant={shown && active >= 0 ? `${id}-${active}` : undefined}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          disabled={disabled}
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
            setActive(-1);
            setOpen(true);
          }}
          onFocus={() => {
            if (quietFocus.current) quietFocus.current = false;
            else setOpen(true);
          }}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          tabIndex={-1}
          className={styles.caret}
          aria-label="Show folders"
          onMouseDown={(event) => {
            event.preventDefault();
            input.current?.focus();
            setOpen((current) => !current);
          }}
        >
          <Icon name="chevron-down" size={16} />
        </button>
        {shown ? (
          <ul className={styles.list} id={`${id}-list`} role="listbox" aria-label="Folders">
            {options.map((option, index) => (
              <li
                key={option}
                id={`${id}-${index}`}
                role="option"
                aria-selected={index === active}
                className={cx(styles.option, index === active && styles.active)}
                // Keeps the focus in the field, so the list does not close before the pick.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => pick(option)}
              >
                <FolderPath path={option} />
              </li>
            ))}
          </ul>
        ) : null}
      </span>
      {error ? <ErrorLine id={`${id}-error`} path={error} /> : null}
    </Field>
  );
}

function FolderRow({ host, token, value, onChange, launchTargetId, recentCwds, error, disabled }: FolderFieldProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const options = useFolderSuggestions(host, token, open ? draft : value, launchTargetId, recentCwds);
  const done = () => {
    onChange(draft);
    setOpen(false);
  };

  // A folder the server rejected opens the sheet on it.
  const [shownError, setShownError] = useState(error);
  if (error !== shownError) {
    setShownError(error);
    if (error) {
      setDraft(error);
      setOpen(true);
    }
  }

  return (
    <>
      <SettingsRow
        label="Folder"
        detail={<FolderPath path={value || "~"} />}
        disabled={disabled}
        onPress={() => {
          setDraft(value);
          setOpen(true);
        }}
      />
      {error && !open ? <ErrorLine path={error} /> : null}
      <Sheet
        open={open}
        onClose={() => setOpen(false)}
        title="Folder"
        footer={
          <Button variant="primary" block onClick={done}>
            Done
          </Button>
        }
      >
        <div className={styles.sheetBody}>
          <TextInput
            mono
            tail
            autoComplete="off"
            data-autofocus=""
            aria-label="Folder"
            aria-invalid={error && draft === error ? true : undefined}
            aria-describedby={error && draft === error ? `${id}-error` : undefined}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                done();
              }
            }}
          />
          {error && draft === error ? <ErrorLine id={`${id}-error`} path={error} /> : null}
          <ul className={styles.sheetList} aria-label="Folders">
            {options.map((option) => (
              <li key={option}>
                <button
                  type="button"
                  className={cx(styles.sheetOption, option === draft && styles.active)}
                  onClick={() => {
                    if (option.endsWith("/")) {
                      setDraft(option);
                      return;
                    }
                    onChange(option);
                    setOpen(false);
                  }}
                >
                  <FolderPath path={option} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      </Sheet>
    </>
  );
}
