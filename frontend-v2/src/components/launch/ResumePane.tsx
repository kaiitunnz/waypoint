"use client";

import { useState } from "react";

import { isAuthError } from "@waypoint/web-core/api";
import type { BackendCatalog } from "@waypoint/web-core/backends";
import { humaniseBackend } from "@waypoint/web-core/backends";
import { envSummary } from "@waypoint/web-core/launch/env";
import { modelChoices } from "@waypoint/web-core/launch/models";
import { deleteThreadMessage, formatThreadTime, threadEmptyHint, threadFilterOptions, type AgentThread } from "@waypoint/web-core/launch/threads";

import { ActionChip } from "@/components/ui/ActionChip";
import { ChoiceField } from "@/components/ui/Choice";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { SearchInput } from "@/components/ui/Field";
import { MenuButton } from "@/components/ui/MenuButton";
import { SwitchRow } from "@/components/ui/Settings";
import { handleAuthFailure } from "@/lib/auth";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import { Disclosure, EnvField } from "./AdvancedFields";
import { transportOptions } from "./options";
import { threadKey, type ResumeState } from "./useResume";
import styles from "./ResumePane.module.css";

interface ResumePaneProps {
  resume: ResumeState;
  catalog: BackendCatalog;
  targetLabel: string | null;
  busy: boolean;
  onDeleteFailed: (message: string) => void;
}

// The interface, model and environment apply only when one agent is chosen.
export function ResumePane({ resume, catalog, targetLabel, busy, onDeleteFailed }: ResumePaneProps) {
  const pointer = usePointerUI();
  const [deleting, setDeleting] = useState<AgentThread | null>(null);
  const label = (agent: string) => humaniseBackend(agent, catalog);
  const filters = threadFilterOptions(resume.backends);
  const agent = resume.agent;

  const env = <EnvField entries={resume.envEntries} onChange={resume.setEnvEntries} disabled={busy} />;
  const settings = (
    <div className={pointer ? styles.grid : styles.rows}>
      {filters.length > 1 ? (
        <ChoiceField
          label="Agent"
          value={resume.filter}
          options={filters.map((filter) => ({
            value: filter,
            label: `${filter === "all" ? "All" : label(filter)}  ${resume.counts[filter] ?? 0} threads`,
          }))}
          onChange={resume.setFilter}
          disabled={busy}
        />
      ) : null}
      {agent && resume.transports.length > 1 ? (
        <ChoiceField
          label="Interface"
          value={resume.transport}
          options={transportOptions(resume.transports, catalog)}
          onChange={resume.setTransport}
          disabled={busy}
        />
      ) : null}
      {agent && resume.showModel && resume.models.options.length > 0 ? (
        <ChoiceField
          label="Model"
          value={resume.models.selected ?? ""}
          options={modelChoices(
            resume.models.options.filter((model) => !model.hidden),
            resume.models.selected,
          ).map((model) => ({ value: model.id, label: model.label }))}
          onChange={resume.models.setSelected}
          disabled={busy}
        />
      ) : null}
    </div>
  );

  return (
    <div className={styles.pane}>
      {settings}
      <div className={styles.rows}>
        <SwitchRow label="Replay history" checked={resume.importHistory} onChange={resume.setImportHistory} />
        {agent ? (
          pointer ? (
            <div className={styles.advanced}>
              <Disclosure title="Advanced" summary={envSummary(resume.envEntries) || "No environment overrides"}>
                {env}
              </Disclosure>
            </div>
          ) : (
            env
          )
        ) : null}
      </div>
      <SearchInput
        aria-label="Filter threads"
        placeholder="Filter threads"
        value={resume.query}
        onChange={(event) => resume.setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && resume.query) {
            event.preventDefault();
            event.nativeEvent.stopImmediatePropagation();
            resume.setQuery("");
          }
        }}
      />
      {resume.loading && resume.threads.length === 0 ? <p className={styles.note}>Loading threads…</p> : null}
      {!resume.loading && resume.threads.length === 0 ? (
        <p className={styles.note}>{resume.query.trim() ? "No threads match." : threadEmptyHint(resume.filter, catalog, targetLabel)}</p>
      ) : null}
      <ul className={styles.threads} role="radiogroup" aria-label="Threads">
        {resume.shown.map((thread) => {
          const selected = resume.selected === thread;
          return (
            <li key={threadKey(thread)} className={cx(styles.thread, selected && styles.selected)}>
              <button
                type="button"
                role="radio"
                aria-checked={selected}
                className={styles.threadButton}
                disabled={busy}
                onClick={() => resume.select(thread)}
              >
                <span className={cx(styles.radio, selected && styles.radioOn)} aria-hidden="true" />
                <span className={styles.threadBody}>
                  <span className={styles.threadHead}>
                    <span className={styles.threadTitle}>{thread.title}</span>
                    <time className={styles.time} dateTime={thread.updated_at}>
                      {formatThreadTime(thread.updated_at)}
                    </time>
                  </span>
                  {thread.preview ? <span className={styles.preview}>{thread.preview}</span> : null}
                  <span className={styles.meta}>
                    {resume.filter === "all" && resume.backends.length > 1 ? <span>{label(thread.backend)}</span> : null}
                    {thread.branch ? <span className={styles.branch}>{thread.branch}</span> : null}
                    {thread.repo_name ? <span>{thread.repo_name}</span> : null}
                  </span>
                </span>
              </button>
              {resume.canDelete(thread) ? (
                <span className={styles.menu}>
                  <MenuButton
                    label={`${thread.title} actions`}
                    title={thread.title}
                    size="sm"
                    items={[{ id: "delete", label: "Delete transcript", tone: "danger" }]}
                    onSelect={() => setDeleting(thread)}
                  />
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>
      {pointer && resume.more > 0 ? (
        <ActionChip icon="chevron-down" onClick={resume.showMore} className={styles.more}>
          {resume.more} more threads
        </ActionChip>
      ) : null}
      <ConfirmDialog
        open={deleting !== null}
        title="Delete transcript"
        detail={deleting ? deleteThreadMessage(deleting) : ""}
        confirmLabel="Delete"
        onConfirm={async () => {
          if (!deleting) return;
          try {
            await resume.remove(deleting);
          } catch (error) {
            if (isAuthError(error)) handleAuthFailure();
            else onDeleteFailed(error instanceof Error ? error.message : "failed to delete the transcript");
          }
        }}
        onClose={() => setDeleting(null)}
      />
    </div>
  );
}
