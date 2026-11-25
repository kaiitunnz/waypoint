"use client";

import { Fragment, useEffect, type RefObject } from "react";

import { attachmentUrl } from "@waypoint/web-core/api";
import { formatFileSize, typeTag } from "@waypoint/web-core/attachments";
import {
  rowDescription,
  rowDescriptionPositions,
  rowHint,
  rowKey,
  rowLabel,
  rowSection,
  type SuggestionRow,
} from "@waypoint/web-core/composer-completions";
import type { FileMentionRow } from "@waypoint/web-core/hooks/use-file-mentions";

import { FileThumb } from "@/components/ui/FileThumb";
import { cx } from "@/lib/cx";

import { HighlightedText } from "./HighlightedText";
import styles from "./SuggestionMenu.module.css";

// The field points at the highlighted row by this id.
export function suggestionId(menuId: string, index: number): string {
  return `${menuId}-${index}`;
}

interface MenuProps {
  id: string;
  activeIndex: number;
  itemRefs: RefObject<Array<HTMLButtonElement | null>>;
  onApply: (index: number) => void;
  onHover: (index: number) => void;
}

// Rows keep the focus in the field, so they act on mouse down.
function rowProps(index: number, { id, activeIndex, itemRefs, onApply, onHover }: MenuProps) {
  return {
    ref: (node: HTMLButtonElement | null) => {
      itemRefs.current[index] = node;
    },
    id: suggestionId(id, index),
    type: "button" as const,
    role: "option",
    "aria-selected": index === activeIndex,
    onMouseDown: (event: { preventDefault: () => void }) => {
      event.preventDefault();
      onApply(index);
    },
    onMouseEnter: () => onHover(index),
  };
}

interface CommandMenuProps extends MenuProps {
  suggestions: ReadonlyArray<SuggestionRow>;
  listRef: RefObject<HTMLUListElement | null>;
}

export function CommandMenu({ suggestions, listRef, ...menu }: CommandMenuProps) {
  return (
    <ul id={menu.id} className={styles.menu} role="listbox" aria-label="Completions" ref={listRef}>
      {suggestions.map((entry, index) => {
        const hint = rowHint(entry);
        const description = rowDescription(entry);
        const workspace = rowSection(entry) === "workspace";
        return (
          <Fragment key={rowKey(entry)}>
            {workspace && rowSection(suggestions[index - 1]) === "dir" ? (
              <li role="presentation" className={styles.section}>
                Across workspace
              </li>
            ) : null}
            <li role="presentation">
              <button {...rowProps(index, menu)} className={cx(styles.row, index === menu.activeIndex && styles.active)}>
                <span className={cx(styles.name, "mono")}>
                  <HighlightedText text={rowLabel(entry)} positions={entry.positions} path={entry.type === "workspace_path"} />
                  {hint ? <span className={styles.hint}> {hint}</span> : null}
                </span>
                {description ? (
                  <span className={styles.description}>
                    <HighlightedText text={description} positions={rowDescriptionPositions(entry)} />
                  </span>
                ) : null}
              </button>
            </li>
          </Fragment>
        );
      })}
    </ul>
  );
}

interface MentionMenuProps extends MenuProps {
  host: string;
  token: string;
  sessionId: string;
  mentions: ReadonlyArray<FileMentionRow>;
}

export function MentionMenu({ host, token, sessionId, mentions, ...menu }: MentionMenuProps) {
  const { activeIndex, itemRefs } = menu;
  useEffect(() => {
    itemRefs.current[activeIndex]?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, itemRefs]);

  return (
    <ul id={menu.id} className={styles.menu} role="listbox" aria-label="Session files">
      {mentions.map(({ file, positions }, index) => (
        <li key={file.id} role="presentation">
          <button {...rowProps(index, menu)} className={cx(styles.row, styles.file, index === menu.activeIndex && styles.active)}>
            <FileThumb
              className={styles.thumb}
              src={file.kind === "image" ? attachmentUrl(host, token, sessionId, file.id) : null}
            />
            <span className={styles.fileText}>
              <span className={styles.name}>
                <HighlightedText text={file.filename} positions={positions} />
              </span>
              <span className={styles.description}>
                <span>{typeTag(file.filename, file.kind === "image")}</span>
                <span>{formatFileSize(file.size)}</span>
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
