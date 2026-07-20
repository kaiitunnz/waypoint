"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import {
  BoardPostSheet,
  type SubmitResult,
  type TicketSubmit,
  type UpdateSubmit,
} from "@/components/board/BoardPostSheet";
import { Sheet } from "@/components/Sheet";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  clearBoardChannel,
  connectSessionsSocket,
  deleteBoardChannel,
  deleteBoardEntry,
  fetchBoardChannel,
  fetchBoardChannels,
  fetchManagers,
  fetchManagerState,
  isAuthError,
  postBoardEntry,
  updateBoardEntry,
} from "@/lib/api";
import {
  CHANNEL_GROUP_COLLAPSED_DEFAULT,
  CHANNEL_GROUP_LABELS,
  CHANNEL_GROUP_ORDER,
  classifyChannel,
  isAwaiting,
  kindTone,
  laneForState,
  LANES,
  priorityTone,
  rollupTickets,
  stateLabel,
  stateTone,
  ticketIdFromChannel,
  type ChannelGroupKey,
} from "@/lib/board";
import { clearToken, readHost, readToken } from "@/lib/store";
import { useTheme } from "@/lib/theme";
import {
  BoardChannel,
  BoardEntry,
  ManagerStateResponse,
  ManagerSummary,
  ManagerTicket,
  SessionEnvelope,
} from "@/lib/types";
import { formatRelativeTime } from "@/lib/usage";

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 15000;
const BOARD_REFRESH_DEBOUNCE_MS = 300;
const LOG_LIMIT = 100;
const MOBILE_BREAKPOINT = 720;
const COLLAPSED_GROUPS_KEY = "waypoint.board.collapsedGroups";
const SELECTED_MANAGER_KEY = "waypoint.board.manager";
const RAIL_COLLAPSED_KEY = "waypoint.board.railCollapsed";

type LoadState = "loading" | "ready" | "error";
type BoardView = "board" | "channels";
type ComposerMode = "closed" | "ticket" | "update";

// A truthful, non-blocking acknowledgement shown after a successful post.
interface PostConfirm {
  text: string;
  action: { label: string; channel: string };
}

function shortId(value: string): string {
  return value.length > 16 ? `${value.slice(0, 16)}…` : value;
}

// A scalar cell holds a short single-line value (a status flag, a count). It
// renders as a compact strip row instead of a card so a wall of one-word cells
// doesn't dominate the board.
const SCALAR_MAX_LEN = 64;
function isScalarCell(entry: BoardEntry): boolean {
  return !entry.text.includes("\n") && entry.text.trim().length <= SCALAR_MAX_LEN;
}

// The brief cell, promoted above the rest of the board.
const HERO_CELL_KEY = "plan";
// The tech-lead feedback cell, promoted into a semantic state banner.
const STATUS_CELL_KEY = "status";

// The common author of a set of entries, or null when they disagree or none
// has one. Lets the board state authorship once instead of on every card.
function uniformAuthor(entries: BoardEntry[]): string | null {
  if (entries.length === 0) return null;
  const first = entries[0].author_session_id;
  if (!first) return null;
  return entries.every((entry) => entry.author_session_id === first)
    ? first
    : null;
}

// ─── Semantic metadata ───

function metaString(
  metadata: Record<string, unknown>,
  key: string,
): string | null {
  const value = metadata[key];
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number") return String(value);
  return null;
}

// Keys rendered as first-class facts (PR/CI/commit) or a tone/tag (kind); the
// rest fall back to chips.
const SEMANTIC_META_KEYS = new Set(["kind", "pr", "pr_url", "checks", "commit"]);

interface SemanticFacts {
  pr: string | null;
  checks: string | null;
  commit: string | null;
  rest: Record<string, unknown>;
}

function semanticFacts(metadata: Record<string, unknown>): SemanticFacts {
  const pr = metaString(metadata, "pr_url") ?? metaString(metadata, "pr");
  const checks = metaString(metadata, "checks");
  const commit = metaString(metadata, "commit");
  const rest: Record<string, unknown> = {};
  for (const key of Object.keys(metadata)) {
    if (!SEMANTIC_META_KEYS.has(key)) rest[key] = metadata[key];
  }
  return { pr, checks, commit, rest };
}

function prNumber(pr: string): string | null {
  const match = pr.match(/\/pull\/(\d+)|#(\d+)|\/(\d+)(?:$|[/?#])/);
  return match ? match[1] ?? match[2] ?? match[3] ?? null : null;
}

// Green/passing → success, failing → danger, running/pending → warn.
function ciTone(checks: string): "success" | "danger" | "warn" | "muted" {
  const value = checks.toLowerCase();
  if (/pass|green|success|ok/.test(value)) return "success";
  if (/fail|red|error|broke/.test(value)) return "danger";
  if (/pend|run|queue|progress/.test(value)) return "warn";
  return "muted";
}

function SemanticFactRow({ facts }: { facts: SemanticFacts }) {
  if (!facts.pr && !facts.checks && !facts.commit) return null;
  const num = facts.pr ? prNumber(facts.pr) : null;
  const isUrl = facts.pr ? /^https?:\/\//.test(facts.pr) : false;
  return (
    <div className="board-facts">
      {facts.pr ? (
        isUrl ? (
          <a
            className="board-fact board-fact-pr"
            href={facts.pr}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            ↗ PR{num ? ` #${num}` : ""}
          </a>
        ) : (
          <span className="board-fact board-fact-pr">
            PR{num ? ` #${num}` : ` ${facts.pr}`}
          </span>
        )
      ) : null}
      {facts.checks ? (
        <span className="board-fact board-fact-ci">
          <span className="board-lamp" data-tone={ciTone(facts.checks)} />
          checks {facts.checks}
        </span>
      ) : null}
      {facts.commit ? (
        <span className="board-fact board-fact-commit">
          ◇ {facts.commit.slice(0, 8)}
        </span>
      ) : null}
    </div>
  );
}

function MetaChips({ metadata }: { metadata: Record<string, unknown> }) {
  const keys = Object.keys(metadata);
  if (keys.length === 0) return null;
  return (
    <div className="board-meta">
      {keys.map((key) => (
        <span key={key} className="board-meta-chip">
          {key}={String(metadata[key])}
        </span>
      ))}
    </div>
  );
}

const stopEvent = (event: { stopPropagation: () => void }) =>
  event.stopPropagation();

interface EntryEditorProps {
  entry: BoardEntry;
  draft: string;
  saving: boolean;
  onChange: (value: string) => void;
  onSave: (entry: BoardEntry) => void;
  onCancel: () => void;
}

// Edits happen in place of the post text so you rewrite the content where it
// lives, not in a detached field. Text-only by design; the post keeps its key
// and metadata.
function EntryEditor({
  entry,
  draft,
  saving,
  onChange,
  onSave,
  onCancel,
}: EntryEditorProps) {
  const unchanged = !draft.trim() || draft === entry.text;
  return (
    <div className="board-edit" onClick={stopEvent}>
      <textarea
        className="board-edit-text"
        value={draft}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          event.stopPropagation();
          if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
          } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            if (!saving && !unchanged) onSave(entry);
          }
        }}
        rows={3}
        autoFocus
        aria-label="Edit post text"
      />
      <div className="board-edit-foot">
        <span className="board-edit-hint" aria-hidden="true">
          ⌘↵ save · esc cancel
        </span>
        <div className="board-edit-actions">
          <button
            type="button"
            className="board-action"
            onClick={onCancel}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="button"
            className="board-action board-edit-save"
            onClick={() => onSave(entry)}
            disabled={saving || unchanged}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface EntryDetailProps {
  entry: BoardEntry;
  confirmingDelete: boolean;
  onStartEdit: (entry: BoardEntry) => void;
  onRequestDelete: (entry: BoardEntry) => void;
  onConfirmDelete: (entry: BoardEntry) => void;
  onCancelDelete: () => void;
}

function EntryDetail({
  entry,
  confirmingDelete,
  onStartEdit,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
}: EntryDetailProps) {
  return (
    <div className="board-detail" onClick={stopEvent} onKeyDown={stopEvent}>
      <dl className="board-detail-rows">
        <div className="board-detail-row">
          <dt>entry</dt>
          <dd>#{entry.id}</dd>
        </div>
        <div className="board-detail-row">
          <dt>posted</dt>
          <dd>{new Date(entry.created_at).toLocaleString()}</dd>
        </div>
        {entry.edited_at ? (
          <div className="board-detail-row">
            <dt>edited</dt>
            <dd>{new Date(entry.edited_at).toLocaleString()}</dd>
          </div>
        ) : null}
        <div className="board-detail-row">
          <dt>author</dt>
          <dd>
            {entry.author_session_id ? (
              <Link
                className="board-detail-link"
                href={`/session/${entry.author_session_id}`}
              >
                {entry.author_session_id} →
              </Link>
            ) : (
              "no session"
            )}
          </dd>
        </div>
      </dl>

      {confirmingDelete ? (
        <div className="board-detail-actions board-confirm">
          <span className="board-confirm-text">Delete this post?</span>
          <button type="button" className="board-action" onClick={onCancelDelete}>
            Cancel
          </button>
          <button
            type="button"
            className="board-action board-action-danger"
            onClick={() => onConfirmDelete(entry)}
          >
            Delete
          </button>
        </div>
      ) : (
        <div className="board-detail-actions">
          <button
            type="button"
            className="board-action"
            onClick={() => onStartEdit(entry)}
          >
            Edit
          </button>
          <button
            type="button"
            className="board-action board-action-danger"
            onClick={() => onRequestDelete(entry)}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

interface EntryControls {
  editingId: number | null;
  editDraft: string;
  savingEdit: boolean;
  expandedId: number | null;
  confirmDeleteId: number | null;
  onEditChange: (value: string) => void;
  onEditSave: (entry: BoardEntry) => void;
  onEditCancel: () => void;
  onStartEdit: (entry: BoardEntry) => void;
  onRequestDelete: (entry: BoardEntry) => void;
  onConfirmDelete: (entry: BoardEntry) => void;
  onCancelDelete: () => void;
}

// Shared body for every expandable entry: the editor swaps in for the value
// while editing, and the detail panel appends while expanded. `children` is the
// value markup, which differs per layout (card, hero, log line). State-strip
// rows render their value inline in the row instead and pass `null`, so they
// get only the editor/detail behavior.
function EntryExpansion({
  entry,
  controls,
  children,
}: {
  entry: BoardEntry;
  controls: EntryControls;
  children: ReactNode;
}) {
  const editing = controls.editingId === entry.id;
  const expanded = controls.expandedId === entry.id;
  return (
    <>
      {editing ? (
        <EntryEditor
          entry={entry}
          draft={controls.editDraft}
          saving={controls.savingEdit}
          onChange={controls.onEditChange}
          onSave={controls.onEditSave}
          onCancel={controls.onEditCancel}
        />
      ) : (
        children
      )}
      {expanded && !editing ? (
        <EntryDetail
          entry={entry}
          confirmingDelete={controls.confirmDeleteId === entry.id}
          onStartEdit={controls.onStartEdit}
          onRequestDelete={controls.onRequestDelete}
          onConfirmDelete={controls.onConfirmDelete}
          onCancelDelete={controls.onCancelDelete}
        />
      ) : null}
    </>
  );
}

// ─── Navigator ───

interface NavigatorProps {
  channels: BoardChannel[];
  activeChannel: string | null;
  search: string;
  onSearch: (value: string) => void;
  collapsed: Set<string>;
  onToggleGroup: (group: string) => void;
  channelTicket: (channel: string) => ManagerTicket | null;
  attention: (channel: string) => boolean;
  onSelect: (channel: string) => void;
}

function Navigator({
  channels,
  activeChannel,
  search,
  onSearch,
  collapsed,
  onToggleGroup,
  channelTicket,
  attention,
  onSelect,
}: NavigatorProps) {
  const grouped = useMemo(() => {
    const filter = search.trim().toLowerCase();
    const groups: Record<ChannelGroupKey, BoardChannel[]> = {
      manager: [],
      ticket: [],
      job: [],
      other: [],
    };
    for (const channel of channels) {
      if (filter && !channel.channel.toLowerCase().includes(filter)) continue;
      groups[classifyChannel(channel.channel)].push(channel);
    }
    return groups;
  }, [channels, search]);

  return (
    <div className="board-nav">
      <div className="board-nav-search">
        <span className="board-nav-search-cue" aria-hidden="true">
          ⌕
        </span>
        <input
          className="board-nav-search-input"
          type="search"
          placeholder="Filter channels"
          value={search}
          onChange={(event) => onSearch(event.target.value)}
          aria-label="Filter channels"
        />
      </div>
      <div className="board-nav-groups">
        {CHANNEL_GROUP_ORDER.map((group) => {
          const rows = grouped[group];
          if (rows.length === 0) return null;
          const isCollapsed = collapsed.has(group);
          const attentionCount = rows.filter((row) =>
            attention(row.channel),
          ).length;
          return (
            <section key={group} className="board-nav-group">
              <button
                type="button"
                className="board-nav-group-head"
                onClick={() => onToggleGroup(group)}
                aria-expanded={!isCollapsed}
              >
                <span className="board-nav-group-chevron" aria-hidden="true">
                  ›
                </span>
                <span className="board-nav-group-label">
                  {CHANNEL_GROUP_LABELS[group]}
                </span>
                {attentionCount > 0 ? (
                  <span
                    className="board-nav-group-attention"
                    title={`${attentionCount} awaiting you`}
                  />
                ) : null}
                <span className="board-nav-group-count">{rows.length}</span>
              </button>
              {isCollapsed ? null : (
                <ul className="board-nav-list">
                  {rows.map((row) => {
                    const ticket = channelTicket(row.channel);
                    const needsYou = attention(row.channel);
                    return (
                      <li key={row.channel}>
                        <button
                          type="button"
                          className={`board-nav-item${
                            row.channel === activeChannel ? " is-active" : ""
                          }`}
                          onClick={() => onSelect(row.channel)}
                        >
                          {ticket ? (
                            <span
                              className="board-lamp board-nav-lamp"
                              data-tone={stateTone(ticket.state)}
                              title={stateLabel(ticket.state)}
                            />
                          ) : (
                            <span className="board-nav-lamp-spacer" />
                          )}
                          <span className="board-nav-name">{row.channel}</span>
                          {needsYou ? (
                            <span
                              className="board-nav-attention"
                              title="Awaiting you"
                            />
                          ) : null}
                          <span className="board-nav-count">
                            {row.entry_count}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}

// ─── Manager ticket board ───

interface TicketCardProps {
  ticket: ManagerTicket;
  onOpen: (ticket: ManagerTicket) => void;
}

function TicketCard({ ticket, onOpen }: TicketCardProps) {
  const awaiting = isAwaiting(ticket.state);
  const num = ticket.pr_url ? prNumber(ticket.pr_url) : null;
  return (
    <article
      className={`board-ticket${awaiting ? " is-awaiting" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => onOpen(ticket)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(ticket);
        }
      }}
    >
      <header className="board-ticket-head">
        <span className="board-ticket-id">#{ticket.id}</span>
        <span
          className="board-ticket-priority"
          data-tone={priorityTone(ticket.priority)}
        >
          {ticket.priority}
        </span>
      </header>
      <p className="board-ticket-title">{ticket.title}</p>
      <footer className="board-ticket-foot">
        <span className="board-ticket-state">
          <span className="board-lamp" data-tone={stateTone(ticket.state)} />
          {stateLabel(ticket.state)}
        </span>
        {ticket.lead_session_id ? (
          <span className="board-ticket-assignee">
            {shortId(ticket.lead_session_id)}
          </span>
        ) : null}
        {ticket.pr_url ? (
          <span className="board-ticket-pr">↗ PR{num ? ` #${num}` : ""}</span>
        ) : null}
      </footer>
    </article>
  );
}

interface ManagerBoardProps {
  managerState: ManagerStateResponse;
  onOpenTicket: (ticket: ManagerTicket) => void;
}

function ManagerBoard({ managerState, onOpenTicket }: ManagerBoardProps) {
  const tickets = managerState.tickets;
  const rollup = useMemo(() => rollupTickets(tickets), [tickets]);
  const needsYou = useMemo(
    () =>
      tickets
        .filter((ticket) => isAwaiting(ticket.state))
        .sort((a, b) => a.priority.localeCompare(b.priority)),
    [tickets],
  );
  const byLane = useMemo(() => {
    const map = new Map<string, ManagerTicket[]>();
    for (const lane of LANES) map.set(lane.key, []);
    for (const ticket of tickets) {
      const lane = laneForState(ticket.state);
      map.get(lane)?.push(ticket);
    }
    return map;
  }, [tickets]);

  return (
    <div className="board-manager">
      <div className="board-rollup" role="status">
        <span className="board-rollup-item board-rollup-need">
          <strong>{rollup.needYou}</strong> need you
        </span>
        <span className="board-rollup-sep" aria-hidden="true">
          ·
        </span>
        <span className="board-rollup-item">
          <strong>{rollup.inFlight}</strong> in flight
        </span>
        <span className="board-rollup-sep" aria-hidden="true">
          ·
        </span>
        <span className="board-rollup-item">
          <strong>{rollup.blocked}</strong> blocked
        </span>
        <span className="board-rollup-sep" aria-hidden="true">
          ·
        </span>
        <span className="board-rollup-item">
          <strong>{rollup.merged}</strong> merged
        </span>
      </div>

      {needsYou.length > 0 ? (
        <section className="board-needs" aria-label="Needs you">
          <h3 className="board-needs-title">Needs you</h3>
          <div className="board-needs-strip">
            {needsYou.map((ticket) => (
              <NeedsYouCard
                key={ticket.id}
                ticket={ticket}
                onOpen={onOpenTicket}
              />
            ))}
          </div>
        </section>
      ) : null}

      <section className="board-lanes" aria-label="Lifecycle board">
        {LANES.map((lane) => {
          const laneTickets = byLane.get(lane.key) ?? [];
          const isDone = lane.key === "done";
          if (laneTickets.length === 0) {
            return (
              <div key={lane.key} className="board-lane is-empty">
                <div className="board-lane-head">
                  <span className="board-lane-label">{lane.label}</span>
                  <span className="board-lane-count">0</span>
                </div>
              </div>
            );
          }
          return (
            <div
              key={lane.key}
              className={`board-lane${isDone ? " is-done" : ""}`}
            >
              <div className="board-lane-head">
                <span className="board-lane-label">{lane.label}</span>
                <span className="board-lane-count">{laneTickets.length}</span>
              </div>
              <div className="board-lane-cards">
                {laneTickets.map((ticket) => (
                  <TicketCard
                    key={ticket.id}
                    ticket={ticket}
                    onOpen={onOpenTicket}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </section>
    </div>
  );
}

function NeedsYouCard({
  ticket,
  onOpen,
}: {
  ticket: ManagerTicket;
  onOpen: (ticket: ManagerTicket) => void;
}) {
  const gate =
    ticket.state === "review_requested"
      ? "PR review"
      : ticket.state === "spec_review"
        ? "Spec review"
        : "Blocked";
  const num = ticket.pr_url ? prNumber(ticket.pr_url) : null;
  const summary =
    ticket.state === "review_requested" && ticket.pr_url
      ? `Review PR${num ? ` #${num}` : ""} and merge, request changes, or abort.`
      : ticket.state === "spec_review"
        ? "Approve the spec, request changes, or reject."
        : "The tech-lead is blocked and needs your decision.";
  return (
    <article className="board-need-card board-dogear">
      <header className="board-need-head">
        <span className="board-need-id">#{ticket.id}</span>
        <span
          className="board-ticket-priority"
          data-tone={priorityTone(ticket.priority)}
        >
          {ticket.priority}
        </span>
        <span className="board-need-gate" data-tone={stateTone(ticket.state)}>
          <span className="board-lamp" data-tone={stateTone(ticket.state)} />
          {gate}
        </span>
      </header>
      <p className="board-need-ticket-title">{ticket.title}</p>
      <p className="board-need-summary">{summary}</p>
      <div className="board-need-actions">
        {ticket.inbox_item_id ? (
          <Link
            className="board-need-link"
            href={`/inbox?item=${encodeURIComponent(ticket.inbox_item_id)}`}
          >
            open in inbox →
          </Link>
        ) : (
          <span className="board-need-link is-disabled">no inbox item</span>
        )}
        <button
          type="button"
          className="board-need-open"
          onClick={() => onOpen(ticket)}
        >
          open channel
        </button>
      </div>
    </article>
  );
}

interface ManagerSwitcherProps {
  managers: ManagerSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

function ManagerSwitcher({
  managers,
  selectedId,
  onSelect,
}: ManagerSwitcherProps) {
  const selected =
    managers.find((manager) => manager.id === selectedId) ?? managers[0];
  if (managers.length === 1) {
    return (
      <div className="board-switcher board-switcher-static">
        <span className="board-switcher-label">project</span>
        <span className="board-switcher-project">{selected.project}</span>
      </div>
    );
  }
  return (
    <label className="board-switcher">
      <span className="board-switcher-label">project</span>
      <span className="board-switcher-select">
        <select
          value={selected.id}
          onChange={(event) => onSelect(event.target.value)}
          aria-label="Select manager project"
        >
          {managers.map((manager) => (
            <option key={manager.id} value={manager.id}>
              {manager.project}
              {manager.attention_count > 0 ? ` (${manager.attention_count})` : ""}
            </option>
          ))}
        </select>
        {selected.attention_count > 0 ? (
          <span
            className="board-switcher-attention"
            title={`${selected.attention_count} awaiting you`}
          />
        ) : null}
      </span>
    </label>
  );
}

// A sidebar-panel glyph: the outer app frame with a divided-off left rail. The
// rail reads as filled when the navigator is open and as a hollow frame when it
// is collapsed, so the icon states the toggle's effect on its own.
function RailToggleIcon({ collapsed }: { collapsed: boolean }) {
  return (
    <svg
      className="board-rail-toggle-icon"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      aria-hidden="true"
    >
      <defs>
        <clipPath id="board-rail-toggle-clip">
          <rect x="2" y="3" width="12" height="10" rx="2.2" />
        </clipPath>
      </defs>
      {collapsed ? null : (
        <rect
          x="2"
          y="3"
          width="4.4"
          height="10"
          fill="currentColor"
          opacity="0.85"
          clipPath="url(#board-rail-toggle-clip)"
        />
      )}
      <rect
        x="2"
        y="3"
        width="12"
        height="10"
        rx="2.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <line
        x1="6.4"
        y1="3.2"
        x2="6.4"
        y2="12.8"
        stroke="currentColor"
        strokeWidth="1.2"
      />
    </svg>
  );
}

function ViewSwitch({
  view,
  onChange,
}: {
  view: BoardView;
  onChange: (view: BoardView) => void;
}) {
  return (
    <div className="board-viewswitch" role="tablist" aria-label="Board view">
      <button
        type="button"
        role="tab"
        aria-selected={view === "board"}
        className={`board-viewswitch-tab${view === "board" ? " is-active" : ""}`}
        onClick={() => onChange("board")}
      >
        Board
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={view === "channels"}
        className={`board-viewswitch-tab${
          view === "channels" ? " is-active" : ""
        }`}
        onClick={() => onChange("channels")}
      >
        Channels
      </button>
    </div>
  );
}

export default function BoardPage() {
  const router = useRouter();
  const { theme } = useTheme();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [channels, setChannels] = useState<BoardChannel[]>([]);
  const [activeChannel, setActiveChannel] = useState<string | null>(null);
  const [entries, setEntries] = useState<BoardEntry[]>([]);
  const [logTotal, setLogTotal] = useState(0);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [posting, setPosting] = useState(false);
  // The unified Post sheet: "closed" when hidden, else the mode it opened in.
  // The sheet owns its own live mode toggle; this only records the open state
  // and the opening default.
  const [composerMode, setComposerMode] = useState<ComposerMode>("closed");
  const [postConfirm, setPostConfirm] = useState<PostConfirm | null>(null);
  // Set synchronously before the first await so two fast clicks can't enqueue a
  // duplicate keyless intake post; the disabled button is only a backstop.
  const postingRef = useRef(false);
  // Initial focus target for the sheet; the child attaches it to the right
  // field for its opening mode.
  const postFocusRef = useRef<HTMLElement | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  // Board workspace: view switch, manager mode, grouped navigator, mobile drawer.
  const [view, setView] = useState<BoardView>("channels");
  const [managers, setManagers] = useState<ManagerSummary[]>([]);
  const [selectedManagerId, setSelectedManagerId] = useState<string | null>(null);
  const [managerState, setManagerState] = useState<ManagerStateResponse | null>(
    null,
  );
  const [search, setSearch] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [navOpen, setNavOpen] = useState(false);
  // The desktop channel rail collapses so the workspace (kanban lanes, detail)
  // can claim the full width; the choice persists across sessions.
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const drawerRef = useRef<HTMLDivElement | null>(null);

  const managerMode = managers.length > 0;

  // Expand/collapse a card. Never collapse the card you're editing (that would
  // drop the draft); activating a different card cancels any open edit/confirm
  // so only one entry is ever interactive at a time.
  const activateEntry = (id: number) => {
    if (editingId === id) return;
    if (editingId !== null) {
      setEditingId(null);
      setEditDraft("");
    }
    setConfirmDeleteId(null);
    setExpandedId((current) => (current === id ? null : id));
  };

  const onEntryKeyDown = (event: KeyboardEvent, id: number) => {
    if (editingId === id) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activateEntry(id);
    }
  };

  // Read inside the live socket handler without making the socket effect
  // depend on (and reconnect on) every channel switch.
  const activeChannelRef = useRef<string | null>(null);
  useEffect(() => {
    activeChannelRef.current = activeChannel;
  }, [activeChannel]);
  const selectedManagerRef = useRef<string | null>(null);
  useEffect(() => {
    selectedManagerRef.current = selectedManagerId;
  }, [selectedManagerId]);

  // A `?channel=` deep link selects that channel once it loads.
  const pendingChannelRef = useRef<string | null>(null);
  // Resolve the initial view once, after channels and managers first load.
  const viewResolvedRef = useRef(false);
  const requestedViewRef = useRef<BoardView | null>(null);
  // `pendingChannelRef` is cleared once the channel is selected, so the view
  // resolver reads this stable flag to open Channels for a deep link.
  const deepLinkRef = useRef(false);

  const handleAuthFailure = useCallback(() => {
    clearToken();
    setToken("");
    router.replace("/");
  }, [router]);

  useEffect(() => {
    const currentHost = readHost();
    const currentToken = readToken();
    setHost(currentHost);
    setToken(currentToken);
    const params = new URLSearchParams(window.location.search);
    const requestedChannel = params.get("channel");
    if (requestedChannel) {
      pendingChannelRef.current = requestedChannel;
      deepLinkRef.current = true;
    }
    const requestedView = params.get("view");
    if (requestedView === "board" || requestedView === "channels") {
      requestedViewRef.current = requestedView;
    }
    try {
      const stored = window.localStorage.getItem(COLLAPSED_GROUPS_KEY);
      if (stored) {
        setCollapsedGroups(new Set(JSON.parse(stored) as string[]));
      } else {
        setCollapsedGroups(
          new Set(
            CHANNEL_GROUP_ORDER.filter(
              (group) => CHANNEL_GROUP_COLLAPSED_DEFAULT[group],
            ),
          ),
        );
      }
      const storedManager = window.localStorage.getItem(SELECTED_MANAGER_KEY);
      if (storedManager) setSelectedManagerId(storedManager);
      if (window.localStorage.getItem(RAIL_COLLAPSED_KEY) === "1") {
        setRailCollapsed(true);
      }
    } catch {
      // localStorage may be unavailable; defaults are fine.
    }
    if (!currentHost || !currentToken) {
      router.replace("/");
    }
  }, [router]);

  // Track the mobile breakpoint so the navigator becomes a drawer and the
  // focus-trap engages only below it.
  useEffect(() => {
    const query = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`);
    const update = () => setIsMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  const refreshChannels = useCallback(async () => {
    if (!host || !token) return;
    try {
      const list = await fetchBoardChannels(host, token);
      setChannels(list);
      setState("ready");
      setActiveChannel((current) => {
        const pending = pendingChannelRef.current;
        if (pending && list.some((c) => c.channel === pending)) {
          pendingChannelRef.current = null;
          return pending;
        }
        if (current && list.some((c) => c.channel === current)) return current;
        return list[0]?.channel ?? null;
      });
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setState("error");
    }
  }, [host, token, handleAuthFailure]);

  const refreshManagers = useCallback(async () => {
    if (!host || !token) return;
    try {
      const list = await fetchManagers(host, token);
      setManagers(list);
      setSelectedManagerId((current) => {
        if (current && list.some((m) => m.id === current)) return current;
        return list[0]?.id ?? null;
      });
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      // Manager read failures degrade to general mode rather than block the page.
      setManagers([]);
    }
  }, [host, token, handleAuthFailure]);

  const refreshManagerState = useCallback(
    async (managerId: string) => {
      if (!host || !token) return;
      try {
        const next = await fetchManagerState(host, token, managerId);
        setManagerState(next);
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setManagerState(null);
      }
    },
    [host, token, handleAuthFailure],
  );

  const refreshEntries = useCallback(
    async (channel: string) => {
      if (!host || !token) return;
      try {
        const page = await fetchBoardChannel(host, token, channel, {
          limit: LOG_LIMIT,
        });
        setEntries(page.entries);
        setLogTotal(page.logTotal);
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to load entries");
      }
    },
    [host, token, handleAuthFailure],
  );

  const loadOlder = useCallback(async () => {
    if (!host || !token || !activeChannel) return;
    const oldestLogId = entries
      .filter((entry) => !entry.key)
      .reduce<number | null>(
        (min, entry) => (min === null || entry.id < min ? entry.id : min),
        null,
      );
    if (oldestLogId === null) return;
    setLoadingOlder(true);
    try {
      const page = await fetchBoardChannel(host, token, activeChannel, {
        limit: LOG_LIMIT,
        before: oldestLogId,
      });
      // Older rows have ids below everything loaded, so prepend keeps the
      // append-log in ascending id order; cells already loaded are untouched.
      setEntries((prev) => [...page.entries, ...prev]);
      setLogTotal(page.logTotal);
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "failed to load older posts");
    } finally {
      setLoadingOlder(false);
    }
  }, [host, token, activeChannel, entries, handleAuthFailure]);

  useEffect(() => {
    void refreshChannels();
    void refreshManagers();
  }, [refreshChannels, refreshManagers]);

  // Resolve the initial view once: `?view=` wins, then a `?channel=` deep link
  // opens Channels, else default to Board.
  useEffect(() => {
    if (viewResolvedRef.current || state !== "ready") return;
    viewResolvedRef.current = true;
    if (requestedViewRef.current) {
      setView(requestedViewRef.current);
    } else if (deepLinkRef.current) {
      setView("channels");
    } else {
      setView("board");
    }
  }, [state]);

  useEffect(() => {
    if (!selectedManagerId) {
      setManagerState(null);
      return;
    }
    try {
      window.localStorage.setItem(SELECTED_MANAGER_KEY, selectedManagerId);
    } catch {
      // ignore
    }
    void refreshManagerState(selectedManagerId);
  }, [selectedManagerId, refreshManagerState]);

  useEffect(() => {
    setExpandedId(null);
    setEditingId(null);
    setEditDraft("");
    setConfirmDeleteId(null);
    if (activeChannel) {
      void refreshEntries(activeChannel);
    } else {
      setEntries([]);
    }
  }, [activeChannel, refreshEntries]);

  // Keep the URL shareable (view + channel) without triggering a navigation.
  useEffect(() => {
    if (state !== "ready") return;
    const params = new URLSearchParams();
    params.set("view", view);
    if (view === "channels" && activeChannel) {
      params.set("channel", activeChannel);
    }
    window.history.replaceState(null, "", `/board?${params.toString()}`);
  }, [view, activeChannel, state]);

  useEffect(() => {
    if (!host || !token) return;
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    // Coalesce bursts of board_update notifications into a single trailing
    // refresh. `entriesDirty` records whether any update in the burst targeted
    // the channel currently in view, so we only refetch entries when needed.
    let boardRefreshTimer: ReturnType<typeof setTimeout> | null = null;
    let entriesDirty = false;
    const scheduleBoardRefresh = () => {
      if (boardRefreshTimer !== null) {
        clearTimeout(boardRefreshTimer);
      }
      boardRefreshTimer = setTimeout(() => {
        boardRefreshTimer = null;
        if (!active) return;
        void refreshChannels();
        // A gate arrival or ticket-state change shows up on the manager board
        // without a reload: refresh the switcher and the selected manager.
        void refreshManagers();
        const manager = selectedManagerRef.current;
        if (manager) void refreshManagerState(manager);
        if (entriesDirty) {
          entriesDirty = false;
          const current = activeChannelRef.current;
          if (current) void refreshEntries(current);
        }
      }, BOARD_REFRESH_DEBOUNCE_MS);
    };

    function connect() {
      socket = connectSessionsSocket(
        host,
        token,
        (message: SessionEnvelope) => {
          if (message.type === "board_update") {
            const channel = message.payload.channel as string | null;
            const current = activeChannelRef.current;
            if (current && (channel === null || channel === current)) {
              entriesDirty = true;
            }
            scheduleBoardRefresh();
          }
          if (message.type === "auth_revoked") {
            handleAuthFailure();
          }
        },
        () => {
          if (active) handleAuthFailure();
        },
        {
          onOpen: () => {
            attempt = 0;
          },
          onClose: () => {
            if (!active) return;
            const delay = Math.min(
              RECONNECT_MAX_MS,
              RECONNECT_BASE_MS * 2 ** attempt,
            );
            attempt += 1;
            reconnectTimer = setTimeout(connect, delay);
          },
        },
      );
    }

    connect();
    return () => {
      active = false;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (boardRefreshTimer !== null) clearTimeout(boardRefreshTimer);
      socket?.close();
    };
  }, [
    host,
    token,
    refreshChannels,
    refreshManagers,
    refreshManagerState,
    refreshEntries,
    handleAuthFailure,
  ]);

  // Close the mobile drawer on Escape and trap focus within it while open.
  useEffect(() => {
    if (!navOpen) return;
    const node = drawerRef.current;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setNavOpen(false);
        return;
      }
      if (event.key !== "Tab" || !node) return;
      const focusables = node.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    // Focus the panel, not the search box, so opening the drawer never pops the
    // mobile keyboard.
    node?.focus();
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [navOpen]);

  const closePost = useCallback(() => setComposerMode("closed"), []);

  const handleSubmitTicket = useCallback(
    async (draft: TicketSubmit): Promise<SubmitResult> => {
      if (postingRef.current) return { ok: false };
      const channel = managerState?.config?.render_context?.tickets_channel;
      // Guard on identity: managerState may still be the previously selected
      // manager's while the new one loads, so never post to a stale intake.
      const ready =
        managerState?.config?.id === selectedManagerId && !!channel;
      if (!host || !token || !channel || !ready) return { ok: false };
      const project = managerState?.config?.project ?? "the project";
      const title = draft.title.trim();
      const details = draft.details.trim();
      const text = details ? `${title}\n\n${details}` : title;
      postingRef.current = true;
      setPosting(true);
      try {
        await postBoardEntry(host, token, channel, {
          text,
          key: null,
          metadata: { priority: draft.priority },
        });
        setComposerMode("closed");
        setPostConfirm({
          text: `Sent to ${project} intake. The manager will register it shortly.`,
          action: { label: "Open intake", channel },
        });
        await refreshChannels();
        void refreshManagerState(selectedManagerId!);
        return { ok: true };
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return { ok: false };
        }
        return {
          ok: false,
          error: err instanceof Error ? err.message : "failed to post ticket",
        };
      } finally {
        postingRef.current = false;
        setPosting(false);
      }
    },
    [
      host,
      token,
      managerState,
      selectedManagerId,
      refreshChannels,
      refreshManagerState,
      handleAuthFailure,
    ],
  );

  const handleSubmitUpdate = useCallback(
    async (draft: UpdateSubmit): Promise<SubmitResult> => {
      if (postingRef.current) return { ok: false };
      const channel = draft.channel.trim();
      const text = draft.text.trim();
      if (!host || !token || !channel || !text) return { ok: false };
      postingRef.current = true;
      setPosting(true);
      try {
        await postBoardEntry(host, token, channel, {
          text,
          key: draft.key?.trim() || null,
        });
        setComposerMode("closed");
        setActiveChannel(channel);
        setView("channels");
        setPostConfirm({
          text: `Posted to ${channel}.`,
          action: { label: "Open channel", channel },
        });
        await refreshChannels();
        await refreshEntries(channel);
        return { ok: true };
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return { ok: false };
        }
        return {
          ok: false,
          error: err instanceof Error ? err.message : "failed to post update",
        };
      } finally {
        postingRef.current = false;
        setPosting(false);
      }
    },
    [host, token, refreshChannels, refreshEntries, handleAuthFailure],
  );

  const handleClear = useCallback(
    async (channel: string) => {
      if (!host || !token) return;
      if (
        !window.confirm(
          `Remove all posts from "${channel}"? The channel stays. This cannot be undone.`,
        )
      ) {
        return;
      }
      setError("");
      try {
        await clearBoardChannel(host, token, channel);
        await refreshChannels();
        await refreshEntries(channel);
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to clear channel");
      }
    },
    [host, token, refreshChannels, refreshEntries, handleAuthFailure],
  );

  const handleDeleteChannel = useCallback(
    async (channel: string) => {
      if (!host || !token) return;
      if (
        !window.confirm(
          `Delete channel "${channel}" and all its posts? This cannot be undone.`,
        )
      ) {
        return;
      }
      setError("");
      try {
        await deleteBoardChannel(host, token, channel);
        await refreshChannels();
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to delete channel");
      }
    },
    [host, token, refreshChannels, handleAuthFailure],
  );

  const startEdit = useCallback((entry: BoardEntry) => {
    setEditingId(entry.id);
    setEditDraft(entry.text);
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setEditDraft("");
  }, []);

  const handleEditSave = useCallback(
    async (entry: BoardEntry) => {
      if (!host || !token || !activeChannel) return;
      const text = editDraft.trim();
      if (!text || text === entry.text) return;
      setSavingEdit(true);
      setError("");
      try {
        // Edits are text-only here; resend the existing metadata so a text
        // change doesn't wipe the post's chips.
        await updateBoardEntry(host, token, activeChannel, entry.id, {
          text,
          metadata: entry.metadata,
        });
        setEditingId(null);
        setEditDraft("");
        await refreshEntries(activeChannel);
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to edit post");
      } finally {
        setSavingEdit(false);
      }
    },
    [host, token, activeChannel, editDraft, refreshEntries, handleAuthFailure],
  );

  const requestDelete = useCallback((entry: BoardEntry) => {
    setConfirmDeleteId(entry.id);
  }, []);

  const cancelDelete = useCallback(() => {
    setConfirmDeleteId(null);
  }, []);

  const handleDeleteEntry = useCallback(
    async (entry: BoardEntry) => {
      if (!host || !token || !activeChannel) return;
      setError("");
      try {
        await deleteBoardEntry(host, token, activeChannel, entry.id);
        setConfirmDeleteId((current) =>
          current === entry.id ? null : current,
        );
        if (expandedId === entry.id) setExpandedId(null);
        if (editingId === entry.id) setEditingId(null);
        await refreshChannels();
        await refreshEntries(activeChannel);
      } catch (err) {
        if (isAuthError(err)) {
          handleAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to delete post");
      }
    },
    [
      host,
      token,
      activeChannel,
      expandedId,
      editingId,
      refreshChannels,
      refreshEntries,
      handleAuthFailure,
    ],
  );

  const toggleGroup = useCallback((group: string) => {
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      try {
        window.localStorage.setItem(
          COLLAPSED_GROUPS_KEY,
          JSON.stringify([...next]),
        );
      } catch {
        // ignore
      }
      return next;
    });
  }, []);

  const toggleRail = useCallback(() => {
    setRailCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(RAIL_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        // ignore
      }
      return next;
    });
  }, []);

  const selectChannel = useCallback(
    (channel: string) => {
      setActiveChannel(channel);
      setView("channels");
      setNavOpen(false);
    },
    [],
  );

  const openTicket = useCallback(
    (ticket: ManagerTicket) => {
      const prefix = managerState?.config?.render_context?.ticket_channel_prefix;
      if (prefix) {
        const channel = `${prefix}${ticket.id}`;
        if (channels.some((c) => c.channel === channel)) {
          selectChannel(channel);
          return;
        }
      }
      if (ticket.inbox_item_id) {
        router.push(`/inbox?item=${encodeURIComponent(ticket.inbox_item_id)}`);
      }
    },
    [managerState, channels, selectChannel, router],
  );

  // Map ticket channels to their manager ticket for navigator lamps + attention.
  const ticketByChannel = useCallback(
    (channel: string): ManagerTicket | null => {
      const prefix = managerState?.config?.render_context?.ticket_channel_prefix;
      const id = ticketIdFromChannel(channel, prefix);
      if (!id) return null;
      return managerState?.tickets.find((t) => t.id === id) ?? null;
    },
    [managerState],
  );

  const channelAttention = useCallback(
    (channel: string): boolean => {
      const ticket = ticketByChannel(channel);
      return ticket ? isAwaiting(ticket.state) : false;
    },
    [ticketByChannel],
  );

  // Ticket posting reads the selected manager's loaded state. Gate on config
  // identity so a project switch (which refetches async) can't post to the
  // old project's intake while the new state is still loading.
  const managerStateForSelection =
    managerState?.config?.id === selectedManagerId ? managerState : null;
  const managerReady =
    managerMode &&
    !!managerStateForSelection?.config?.render_context?.tickets_channel;
  const managerMisconfigured =
    managerMode &&
    managerStateForSelection !== null &&
    !managerStateForSelection.config?.render_context?.tickets_channel;
  const selectedProject =
    managers.find((manager) => manager.id === selectedManagerId)?.project ??
    null;
  const ticketsChannel =
    managerStateForSelection?.config?.render_context?.tickets_channel ?? null;
  const priorityLevels =
    managerStateForSelection?.config?.priority_levels ?? [];
  const defaultUpdateChannel = activeChannel ?? channels[0]?.channel ?? null;

  // Toolbar Post follows the view: Ticket on a manager Board, Update on
  // Channels. Update opens directly for a channel shortcut or the empty state.
  const openPost = useCallback(() => {
    setPostConfirm(null);
    setComposerMode(view === "board" && managerMode ? "ticket" : "update");
  }, [view, managerMode]);

  const openUpdate = useCallback(() => {
    setPostConfirm(null);
    setComposerMode("update");
  }, []);

  // The two shapes the board is built on: keyed cells (latest-wins
  // variables, pinned) and the append-only log (newest first).
  const cells = entries
    .filter((entry) => entry.key)
    .sort((a, b) => (a.key ?? "").localeCompare(b.key ?? ""));
  const log = entries.filter((entry) => !entry.key).reverse();
  const hasOlder = log.length < logTotal;

  // Cells are ranked by shape rather than rendered uniformly: `status` is the
  // tech-lead feedback banner, `plan` is the brief hero, short scalars collapse
  // into a state strip, the rest stay as cards.
  const statusCell = cells.find((entry) => entry.key === STATUS_CELL_KEY) ?? null;
  const heroCell = cells.find((entry) => entry.key === HERO_CELL_KEY) ?? null;
  const bodyCells = cells.filter(
    (entry) => entry !== heroCell && entry !== statusCell,
  );
  const scalarCells = bodyCells.filter(isScalarCell);
  const cardCells = bodyCells.filter((entry) => !isScalarCell(entry));
  // When every cell shares an author, name them once above the grid and drop
  // the repeated per-card line. Cells always load in full, so this is stable;
  // the log is paginated, so only collapse its author once the whole log is
  // loaded — otherwise "Load older" could reveal a new author and make the
  // header flip away mid-session.
  const cellAuthor = uniformAuthor(cells);
  const logAuthor = hasOlder ? null : uniformAuthor(log);

  // Two-column split (cells | log) on wide screens when both halves have content.
  const activeChannelMeta =
    channels.find((c) => c.channel === activeChannel) ?? null;
  const hasPrimary = statusCell !== null || cells.length > 0;
  const splitDetail = hasPrimary && log.length > 0;

  const controls: EntryControls = {
    editingId,
    editDraft,
    savingEdit,
    expandedId,
    confirmDeleteId,
    onEditChange: setEditDraft,
    onEditSave: handleEditSave,
    onEditCancel: cancelEdit,
    onStartEdit: startEdit,
    onRequestDelete: requestDelete,
    onConfirmDelete: handleDeleteEntry,
    onCancelDelete: cancelDelete,
  };

  const navigator = (
    <Navigator
      channels={channels}
      activeChannel={activeChannel}
      search={search}
      onSearch={setSearch}
      collapsed={collapsedGroups}
      onToggleGroup={toggleGroup}
      channelTicket={ticketByChannel}
      attention={channelAttention}
      onSelect={selectChannel}
    />
  );

  return (
    <main className="page-shell board-shell">
      <header className="app-bar">
        <div className="app-bar-brand">
          <Link className="app-bar-mark" href="/" aria-label="Waypoint home">
            <Image
              src={theme === "light" ? "/waypoint-light.svg" : "/waypoint.svg"}
              alt=""
              width={38}
              height={38}
              priority
            />
          </Link>
          <div className="app-bar-titles">
            <p className="app-bar-eyebrow">Waypoint · board</p>
            <h1 className="app-bar-title">Blackboard</h1>
          </div>
        </div>
        <div className="app-bar-meta">
          {/* The view switch and nav controls live in the workspace toolbar
              below, not on the app bar. */}
          <Link className="back-link" href="/">
            ← all sessions
          </Link>
          <ThemeToggle />
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          <button
            className="error-banner-dismiss"
            onClick={() => setError("")}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ) : null}

      {postConfirm ? (
        <div className="board-post-confirm" role="status">
          <span className="board-post-confirm-text">{postConfirm.text}</span>
          <div className="board-post-confirm-actions">
            <button
              type="button"
              className="board-post-confirm-link"
              onClick={() => {
                selectChannel(postConfirm.action.channel);
                setPostConfirm(null);
              }}
            >
              {postConfirm.action.label} →
            </button>
            <button
              type="button"
              className="board-post-confirm-dismiss"
              onClick={() => setPostConfirm(null)}
              aria-label="Dismiss"
            >
              ×
            </button>
          </div>
        </div>
      ) : null}

      {state === "ready" && (channels.length > 0 || managerMode) ? (
        <div className="board-toolbar">
          {isMobile ? (
            <button
              type="button"
              className="board-toolbar-nav"
              onClick={() => setNavOpen(true)}
              aria-label="Open channels"
            >
              <span aria-hidden="true">☰</span>
            </button>
          ) : (
            <button
              type="button"
              className="board-toolbar-nav board-rail-toggle"
              onClick={toggleRail}
              aria-pressed={!railCollapsed}
              aria-label={
                railCollapsed ? "Show channels sidebar" : "Hide channels sidebar"
              }
              title={railCollapsed ? "Show channels" : "Hide channels"}
            >
              <RailToggleIcon collapsed={railCollapsed} />
            </button>
          )}
          <span className="board-toolbar-context">
            {view === "board"
              ? managerMode
                ? "Ticket board"
                : "Overview"
              : (activeChannel ?? "—")}
          </span>
          <ViewSwitch view={view} onChange={setView} />
          <div className="board-toolbar-actions">
            {managerMode && managers.length > 0 ? (
              <ManagerSwitcher
                managers={managers}
                selectedId={selectedManagerId}
                onSelect={setSelectedManagerId}
              />
            ) : null}
            <button
              type="button"
              className="primary board-toolbar-post"
              onClick={openPost}
            >
              <span className="board-toolbar-post-cue" aria-hidden="true">
                ＋
              </span>
              Post
            </button>
          </div>
        </div>
      ) : null}

      {state === "ready" && channels.length === 0 && !managerMode ? (
        <section className="panel bordered board-empty">
          <h2>The board is empty</h2>
          <p className="muted">
            Nothing has been posted yet. Sessions post with{" "}
            <code>waypoint board post &lt;channel&gt; &lt;message&gt;</code>, or
            post one now.
          </p>
          <button type="button" className="primary" onClick={openUpdate}>
            <span className="board-toolbar-post-cue" aria-hidden="true">
              ＋
            </span>
            Post to a channel
          </button>
        </section>
      ) : null}

      {state === "ready" && (channels.length > 0 || managerMode) ? (
        <section
          className={`board-grid${
            !isMobile && railCollapsed ? " is-rail-collapsed" : ""
          }`}
        >
          {/* Desktop navigator; on mobile this is hidden and the drawer takes over. */}
          {!isMobile ? (
            <aside className="panel board-rail" aria-label="Channels">
              {navigator}
            </aside>
          ) : null}

          {isMobile && navOpen ? (
            <div className="board-drawer-scrim" onClick={() => setNavOpen(false)}>
              <div
                className="board-drawer"
                ref={drawerRef}
                role="dialog"
                aria-modal="true"
                aria-label="Channels"
                tabIndex={-1}
                onClick={stopEvent}
              >
                <div className="board-drawer-head">
                  <h2 className="board-drawer-title">Channels</h2>
                  <button
                    type="button"
                    className="board-drawer-close"
                    onClick={() => setNavOpen(false)}
                    aria-label="Close channels"
                  >
                    ×
                  </button>
                </div>
                {navigator}
              </div>
            </div>
          ) : null}

          <div className="board-main">
            {view === "board" ? (
              <div className="board-workspace">
                <div className="board-context">
                  <div className="board-context-titles">
                    <p className="board-main-eyebrow">workspace</p>
                    <h2 className="board-main-title">
                      {managerMode ? "Ticket board" : "Channels overview"}
                    </h2>
                  </div>
                  {/* The toolbar project switcher is hidden on mobile for space;
                      surface it here, in the always-visible workspace header. */}
                  {managerMode && managers.length > 0 ? (
                    <div className="board-context-project">
                      <ManagerSwitcher
                        managers={managers}
                        selectedId={selectedManagerId}
                        onSelect={setSelectedManagerId}
                      />
                    </div>
                  ) : null}
                </div>

                {managerMode && managerState ? (
                  <ManagerBoard
                    managerState={managerState}
                    onOpenTicket={openTicket}
                  />
                ) : managerMode ? (
                  <p className="board-log-empty">Loading manager board…</p>
                ) : (
                  <ChannelsOverview
                    channels={channels}
                    onSelect={selectChannel}
                  />
                )}
              </div>
            ) : (
              <div className="board-detail-view">
                {activeChannel ? (
                  <header className="board-channel-header">
                    <div className="board-channel-headmain">
                      <p className="board-main-eyebrow">channel</p>
                      <h2 className="board-channel-title">{activeChannel}</h2>
                      <div className="board-channel-summary">
                        <span>
                          {cells.length} {cells.length === 1 ? "cell" : "cells"}
                        </span>
                        <span aria-hidden="true">·</span>
                        <span>
                          {logTotal} {logTotal === 1 ? "post" : "posts"}
                        </span>
                        {activeChannelMeta ? (
                          <>
                            <span aria-hidden="true">·</span>
                            <span>
                              updated{" "}
                              {formatRelativeTime(
                                activeChannelMeta.last_created_at,
                              )}
                            </span>
                          </>
                        ) : null}
                      </div>
                    </div>
                    <div className="board-actions">
                      <button
                        type="button"
                        className="board-action board-action-post"
                        onClick={openUpdate}
                      >
                        Post here
                      </button>
                      <button
                        type="button"
                        className="board-action"
                        onClick={() => void handleClear(activeChannel)}
                      >
                        Clear posts
                      </button>
                      <button
                        type="button"
                        className="board-action board-action-danger"
                        onClick={() => void handleDeleteChannel(activeChannel)}
                      >
                        Delete channel
                      </button>
                    </div>
                  </header>
                ) : null}

                <div
                  className={`board-detail-columns${
                    splitDetail ? " is-split" : ""
                  }`}
                >
                  {hasPrimary ? (
                    <div className="board-detail-primary">
                      {statusCell ? (
                        <StatusBanner
                          entry={statusCell}
                          controls={controls}
                          expanded={expandedId === statusCell.id}
                          onActivate={() => activateEntry(statusCell.id)}
                          onKeyDown={(event) =>
                            onEntryKeyDown(event, statusCell.id)
                          }
                        />
                      ) : null}

                      {cells.length > 0 ? (
                        <section className="board-cells" aria-label="Cells">
                          <h3 className="board-group-label">
                            Cells · latest value
                            {cellAuthor ? (
                              <span className="board-group-by">
                                {" · all by "}
                                {shortId(cellAuthor)}
                              </span>
                            ) : null}
                          </h3>

                          {heroCell ? (
                            <article
                              className={`board-hero${
                                expandedId === heroCell.id ? " is-expanded" : ""
                              }`}
                              role="button"
                              tabIndex={0}
                              aria-expanded={expandedId === heroCell.id}
                              onClick={() => activateEntry(heroCell.id)}
                              onKeyDown={(event) => onEntryKeyDown(event, heroCell.id)}
                            >
                              <header className="board-hero-head">
                                <span className="board-hero-key">{heroCell.key}</span>
                                <span className="board-cell-time">
                                  {formatRelativeTime(heroCell.created_at)}
                                  {heroCell.edited_at ? (
                                    <span className="board-edited"> · edited</span>
                                  ) : null}
                                  <span
                                    className="board-expand-cue"
                                    aria-hidden="true"
                                  >
                                    ›
                                  </span>
                                </span>
                              </header>
                              <EntryExpansion entry={heroCell} controls={controls}>
                                <>
                                  <p className="board-hero-value">{heroCell.text}</p>
                                  <MetaChips metadata={heroCell.metadata} />
                                </>
                              </EntryExpansion>
                            </article>
                          ) : null}

                          {scalarCells.length > 0 ? (
                            <ul className="board-state-list">
                              {scalarCells.map((entry) => (
                                <li key={entry.id} className="board-state-wrap">
                                  <div
                                    className={`board-state-row${
                                      expandedId === entry.id ? " is-expanded" : ""
                                    }`}
                                    role="button"
                                    tabIndex={0}
                                    aria-expanded={expandedId === entry.id}
                                    onClick={() => activateEntry(entry.id)}
                                    onKeyDown={(event) =>
                                      onEntryKeyDown(event, entry.id)
                                    }
                                  >
                                    <span className="board-state-key">
                                      {entry.key}
                                    </span>
                                    {editingId === entry.id ? null : (
                                      <span className="board-state-value">
                                        {entry.text}
                                      </span>
                                    )}
                                    <MetaChips metadata={entry.metadata} />
                                    {cellAuthor ? null : (
                                      <span className="board-state-author">
                                        {entry.author_session_id
                                          ? shortId(entry.author_session_id)
                                          : "—"}
                                      </span>
                                    )}
                                    <span className="board-state-time">
                                      {formatRelativeTime(entry.created_at)}
                                      {entry.edited_at ? (
                                        <span className="board-edited"> · edited</span>
                                      ) : null}
                                      <span
                                        className="board-expand-cue"
                                        aria-hidden="true"
                                      >
                                        ›
                                      </span>
                                    </span>
                                  </div>
                                  <EntryExpansion entry={entry} controls={controls}>
                                    {null}
                                  </EntryExpansion>
                                </li>
                              ))}
                            </ul>
                          ) : null}

                          {cardCells.length > 0 ? (
                            <div className="board-cell-grid">
                              {cardCells.map((entry) => (
                                <article
                                  key={entry.id}
                                  className={`board-cell${
                                    expandedId === entry.id ? " is-expanded" : ""
                                  }`}
                                  role="button"
                                  tabIndex={0}
                                  aria-expanded={expandedId === entry.id}
                                  onClick={() => activateEntry(entry.id)}
                                  onKeyDown={(event) =>
                                    onEntryKeyDown(event, entry.id)
                                  }
                                >
                                  <header className="board-cell-head">
                                    <span className="board-cell-key">
                                      {entry.key}
                                    </span>
                                    <span className="board-cell-time">
                                      {formatRelativeTime(entry.created_at)}
                                      {entry.edited_at ? (
                                        <span className="board-edited"> · edited</span>
                                      ) : null}
                                      <span
                                        className="board-expand-cue"
                                        aria-hidden="true"
                                      >
                                        ›
                                      </span>
                                    </span>
                                  </header>
                                  <EntryExpansion entry={entry} controls={controls}>
                                    <>
                                      <p className="board-cell-value">{entry.text}</p>
                                      {cellAuthor ? null : (
                                        <footer className="board-cell-foot">
                                          <span className="board-cell-author">
                                            {entry.author_session_id
                                              ? shortId(entry.author_session_id)
                                              : "—"}
                                          </span>
                                        </footer>
                                      )}
                                      <SemanticFactRow
                                        facts={semanticFacts(entry.metadata)}
                                      />
                                      <MetaChips
                                        metadata={semanticFacts(entry.metadata).rest}
                                      />
                                    </>
                                  </EntryExpansion>
                                </article>
                              ))}
                            </div>
                          ) : null}
                        </section>
                      ) : null}
                    </div>
                  ) : null}

                  <div className="board-detail-secondary">
                    <section className="board-log" aria-label="Log">
                      {log.length > 0 || hasPrimary ? (
                        <h3 className="board-group-label">
                          Log · newest first
                          {logAuthor ? (
                            <span className="board-group-by">
                              {" · all by "}
                              {shortId(logAuthor)}
                            </span>
                          ) : null}
                        </h3>
                      ) : null}
                      {log.length === 0 ? (
                        <p className="board-log-empty">
                          {cells.length > 0
                            ? "No log posts in this channel."
                            : "No entries yet."}
                        </p>
                      ) : (
                        <ol className="board-log-list">
                          {log.map((entry) => {
                            const kind = metaString(entry.metadata, "kind");
                            const tone = kindTone(kind);
                            const facts = semanticFacts(entry.metadata);
                            const isRelay = kind === "relay";
                            return (
                              <li
                                key={entry.id}
                                className={`board-log-item${
                                  isRelay ? " is-relay" : ""
                                }`}
                                data-tone={tone}
                              >
                                <div className="board-log-rail" aria-hidden="true" />
                                <div
                                  className={`board-log-body${
                                    expandedId === entry.id ? " is-expanded" : ""
                                  }`}
                                  role="button"
                                  tabIndex={0}
                                  aria-expanded={expandedId === entry.id}
                                  onClick={() => activateEntry(entry.id)}
                                  onKeyDown={(event) =>
                                    onEntryKeyDown(event, entry.id)
                                  }
                                >
                                  <EntryExpansion entry={entry} controls={controls}>
                                    <div className="board-log-main">
                                      {kind ? (
                                        <span
                                          className="board-log-tag"
                                          data-tone={tone}
                                        >
                                          {isRelay ? "from you (via inbox)" : kind}
                                        </span>
                                      ) : null}
                                      <p className="board-log-text">{entry.text}</p>
                                      <SemanticFactRow facts={facts} />
                                      <div className="board-log-meta">
                                        <span className="board-log-time">
                                          {formatRelativeTime(entry.created_at)}
                                          {entry.edited_at ? (
                                            <span className="board-edited">
                                              {" "}
                                              · edited
                                            </span>
                                          ) : null}
                                        </span>
                                        {logAuthor ? null : (
                                          <span className="board-log-author">
                                            {entry.author_session_id
                                              ? shortId(entry.author_session_id)
                                              : "—"}
                                          </span>
                                        )}
                                        <MetaChips metadata={facts.rest} />
                                      </div>
                                    </div>
                                  </EntryExpansion>
                                </div>
                              </li>
                            );
                          })}
                        </ol>
                      )}
                      {hasOlder ? (
                        <div className="board-log-more">
                          <button
                            type="button"
                            className="board-load-older"
                            onClick={() => void loadOlder()}
                            disabled={loadingOlder}
                          >
                            {loadingOlder ? "Loading…" : "Load older"}
                          </button>
                          <span className="board-log-count">
                            {log.length} of {logTotal}
                          </span>
                        </div>
                      ) : null}
                    </section>
                  </div>
                </div>
              </div>
            )}
          </div>
        </section>
      ) : null}

      {state === "loading" ? (
        <section className="panel bordered board-empty" aria-busy="true">
          <p className="muted">Loading board…</p>
        </section>
      ) : null}

      {state === "error" ? (
        <section className="panel bordered board-empty">
          <h2>Couldn’t load the board</h2>
          <p className="muted">
            The backend didn’t respond. Check that Waypoint is running, then
            retry.
          </p>
          <button
            type="button"
            className="primary"
            onClick={() => void refreshChannels()}
          >
            Retry
          </button>
        </section>
      ) : null}

      <Sheet
        open={composerMode !== "closed"}
        onClose={closePost}
        eyebrow={view === "board" && managerMode ? "Board · post" : "Channels · post"}
        title="Post"
        initialFocusRef={postFocusRef}
      >
        {composerMode !== "closed" ? (
          <BoardPostSheet
            initialMode={composerMode}
            initialFocusRef={postFocusRef}
            ticketAvailable={managerMode}
            managerReady={managerReady}
            managerMisconfigured={managerMisconfigured}
            project={selectedProject}
            ticketsChannel={ticketsChannel}
            priorityLevels={priorityLevels}
            managers={managers}
            selectedManagerId={selectedManagerId}
            onSelectManager={setSelectedManagerId}
            channels={channels}
            defaultUpdateChannel={defaultUpdateChannel}
            posting={posting}
            onSubmitTicket={handleSubmitTicket}
            onSubmitUpdate={handleSubmitUpdate}
          />
        ) : null}
      </Sheet>
    </main>
  );
}

// ─── Semantic status banner ───

interface StatusBannerProps {
  entry: BoardEntry;
  controls: EntryControls;
  expanded: boolean;
  onActivate: () => void;
  onKeyDown: (event: KeyboardEvent) => void;
}

function StatusBanner({
  entry,
  controls,
  expanded,
  onActivate,
  onKeyDown,
}: StatusBannerProps) {
  const kind = metaString(entry.metadata, "kind");
  const tone = kindTone(kind);
  const facts = semanticFacts(entry.metadata);
  return (
    <article
      className={`board-status-banner${expanded ? " is-expanded" : ""}`}
      data-tone={tone}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      onClick={onActivate}
      onKeyDown={onKeyDown}
    >
      <header className="board-status-head">
        <span className="board-status-lamp">
          <span className="board-lamp" data-tone={tone} />
          <span className="board-status-kind">{kind ?? "status"}</span>
        </span>
        <span className="board-cell-time">
          {formatRelativeTime(entry.created_at)}
          {entry.edited_at ? (
            <span className="board-edited"> · edited</span>
          ) : null}
          <span className="board-expand-cue" aria-hidden="true">
            ›
          </span>
        </span>
      </header>
      <EntryExpansion entry={entry} controls={controls}>
        <>
          <p className="board-status-text">{entry.text}</p>
          <SemanticFactRow facts={facts} />
          <MetaChips metadata={facts.rest} />
        </>
      </EntryExpansion>
    </article>
  );
}

// ─── General channels overview (no manager) ───

function ChannelsOverview({
  channels,
  onSelect,
}: {
  channels: BoardChannel[];
  onSelect: (channel: string) => void;
}) {
  const grouped = useMemo(() => {
    const groups: Record<ChannelGroupKey, BoardChannel[]> = {
      manager: [],
      ticket: [],
      job: [],
      other: [],
    };
    for (const channel of channels) {
      groups[classifyChannel(channel.channel)].push(channel);
    }
    return groups;
  }, [channels]);

  return (
    <div className="board-overview">
      {CHANNEL_GROUP_ORDER.map((group) => {
        const rows = grouped[group];
        if (rows.length === 0) return null;
        const total = rows.reduce((sum, row) => sum + row.entry_count, 0);
        return (
          <section key={group} className="board-overview-group">
            <div className="board-overview-head">
              <h3 className="board-overview-label">
                {CHANNEL_GROUP_LABELS[group]}
              </h3>
              <span className="board-overview-rollup">
                {rows.length} channels · {total} posts
              </span>
            </div>
            <div className="board-overview-cards">
              {rows.map((row) => (
                <button
                  key={row.channel}
                  type="button"
                  className="board-overview-card"
                  onClick={() => onSelect(row.channel)}
                >
                  <span className="board-overview-name">{row.channel}</span>
                  <span className="board-overview-meta">
                    <span className="board-overview-count">
                      {row.entry_count}
                    </span>
                    <span className="board-overview-time">
                      {formatRelativeTime(row.last_created_at)}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
