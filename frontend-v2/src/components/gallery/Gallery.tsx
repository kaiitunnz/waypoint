"use client";

import { useRef, useState } from "react";

import { SessionTime } from "@/components/sessions/SessionTime";
import { ActionChip } from "@/components/ui/ActionChip";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Modal, Sheet } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Field, PasswordInput, SearchInput, TailInput, TextArea, TextInput } from "@/components/ui/Field";
import { FileName } from "@/components/ui/FileName";
import { FileThumb } from "@/components/ui/FileThumb";
import { Flyout } from "@/components/ui/Flyout";
import { GlassBar } from "@/components/ui/GlassBar";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { InlineCode } from "@/components/ui/InlineCode";
import { SummaryText } from "@/components/ui/SummaryText";
import { List, ListRow } from "@/components/ui/List";
import { MenuButton } from "@/components/ui/MenuButton";
import { Meter } from "@/components/ui/Meter";
import { Menu, MenuItem, Popover } from "@/components/ui/Popover";
import { SectionHeading } from "@/components/ui/SectionHeading";
import { Select } from "@/components/ui/Select";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { ChoiceField } from "@/components/ui/Choice";
import { LinkRow, PickerRow, SettingsRow, SettingsSection, SwitchRow } from "@/components/ui/Settings";
import { StatusLamp, type LampState } from "@/components/ui/StatusLamp";
import { useToast } from "@/components/ui/Toast";
import { Toggle } from "@/components/ui/Toggle";

import { PageHeader } from "@/components/ui/PageHeader";
import styles from "./Gallery.module.css";

const LAMPS: LampState[] = ["needs", "working", "error", "idle", "exited", "online", "offline", "unknown"];

export function Gallery() {
  const { toast } = useToast();
  const [sheet, setSheet] = useState(false);
  const [modal, setModal] = useState(false);
  const [popover, setPopover] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [wide, setWide] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const [flyout, setFlyout] = useState(false);
  const flyoutTrigger = useRef<HTMLButtonElement>(null);
  const [effort, setEffort] = useState("high");
  const [segment, setSegment] = useState("all");
  const [view, setView] = useState("chat");
  const [density, setDensity] = useState("summary");
  const [folded, setFolded] = useState(true);
  const [on, setOn] = useState(true);
  const [choice, setChoice] = useState("high");
  const [picked, setPicked] = useState("Lock around release");

  return (
    <div className={styles.page}>
      <PageHeader title="Gallery" subtitle="Primitives" />

      <SectionHeading title="Surfaces" />
      <div className={styles.tiers}>
        <div className={styles.ground}>Ground</div>
        <div className={styles.plane}>Plane</div>
        <div className={styles.raised}>Raised</div>
        <div className={styles.attention}>Attention</div>
      </div>

      <SectionHeading title="Buttons" />
      <div className={styles.row}>
        <Button variant="primary">Approve</Button>
        <Button>Secondary</Button>
        <Button variant="text">For this session</Button>
        <Button variant="danger">Decline</Button>
        <Button variant="primary" busy>
          Connecting
        </Button>
        <Button disabled>Disabled</Button>
        <IconButton label="Search">
          <Icon name="search" />
        </IconButton>
        <IconButton label="More" size="sm">
          <Icon name="more" />
        </IconButton>
      </div>

      <SectionHeading title="Chips" />
      <div className={styles.row}>
        {["Lock around release", "Retry on conflict", "Both"].map((label) => (
          <Chip
            key={label}
            recommended={label === "Lock around release"}
            selected={picked === label}
            onClick={() => setPicked(label)}
          >
            {label}
          </Chip>
        ))}
        <Chip quiet>Other…</Chip>
      </div>

      <SectionHeading title="Segmented" />
      <div className={styles.col}>
        <SegmentedControl
          label="Source"
          value={segment}
          onChange={setSegment}
          segments={[
            { value: "all", label: "All" },
            { value: "sessions", label: "Sessions" },
            { value: "inbox", label: "Inbox", count: 3 },
            { value: "board", label: "Board" },
          ]}
        />
        <SegmentedControl
          label="Density"
          size="sm"
          value={density}
          onChange={setDensity}
          segments={[
            { value: "summary", label: "Summary" },
            { value: "full", label: "Full" },
          ]}
        />
        <SegmentedControl
          label="View"
          size="icon"
          value={view}
          onChange={setView}
          segments={[
            { value: "chat", label: <Icon name="sessions" size={18} />, ariaLabel: "Chat" },
            { value: "terminal", label: <Icon name="ssh" size={18} />, ariaLabel: "Terminal" },
          ]}
        />
      </div>

      <SectionHeading title="Actions in content" />
      <div className={styles.row}>
        <ActionChip icon="chevron-up">Load older messages</ActionChip>
        <ActionChip icon={folded ? "chevron-down" : "chevron-up"} aria-expanded={!folded} onClick={() => setFolded(!folded)}>
          {folded ? "Show all 63 lines" : "Show less"}
        </ActionChip>
        <ActionChip icon="refresh" disabled>
          Retry
        </ActionChip>
        <span>
          Running <InlineCode>npm run lint</InlineCode>
        </span>
        <span>
          <SummaryText text="Read `SKILL.md`" />
        </span>
      </div>

      <SectionHeading title="Toggle, lamps, badges" />
      <div className={styles.row}>
        <Toggle checked={on} onChange={setOn} label="Focus" />
        <Toggle checked={false} onChange={setOn} label="Focus, saving" disabled />
        {LAMPS.map((state) => (
          <span key={state} className={styles.lamp}>
            <StatusLamp state={state} />
            {state}
          </span>
        ))}
        <Badge count={5} />
        <Badge count={120} />
        <span className={styles.lamp}>
          <StatusLamp state="needs" flat />
          needs, flat
        </span>
      </div>

      <SectionHeading title="Meters" dense />
      <div className={styles.fields}>
        <Meter label="Task progress" value={57} className={styles.meter} />
        <Meter label="Calm" value={30} tone="calm" className={styles.meter} />
        <Meter label="Approaching the limit" value={77} tone="warn" className={styles.meter} />
        <Meter label="At the limit" value={96} tone="danger" className={styles.meter} />
        <Meter label="Unknown" value={null} className={styles.meter} />
      </div>

      <SectionHeading title="List rows" count={7} action="See all" />
      <List>
        <ListRow
          lamp="needs"
          unread
          title="Fix held-message race"
          status="Which fix should I ship?"
          trailing={<SessionTime time="09:16" unread />}
        />
        <ListRow
          lamp="idle"
          unread
          title="Queue backoff, unread"
          status="Claude Code, Opus 4.8"
          trailing={<SessionTime time="12m" unread />}
        />
        <ListRow
          lamp="idle"
          unread={false}
          title="Paper figures"
          status="Codex, waypoint"
          trailing={<SessionTime time="Yesterday" pinned />}
        />
        <ListRow lamp="error" unread={false} title="Nightly sweep" status="Claude Code, main" trailing="Mon" />
        <ListRow lamp="exited" unread={false} dimmed title="Old release notes" status="Claude Code, main" trailing="Nov 20" />
        <ListRow
          lamp="working"
          unread={false}
          title="Heatmap keyboard access"
          status="Running npm run lint"
          trailing="2m"
          href="/gallery"
          actions={
            <MenuButton
              label="Row actions"
              title="Heatmap keyboard access"
              subtitle="Claude Code, Opus 4.8"
              header={<SegmentedControl label="Density" value={density} onChange={setDensity} segments={[{ value: "summary", label: "Summary" }, { value: "full", label: "Full" }]} />}
              items={[
                { id: "pin", label: "Pin" },
                { id: "terminate", label: "Terminate…", tone: "danger" },
              ]}
              onSelect={() => {}}
            />
          }
        />
        <ListRow lamp="exited" title="597 exited" titleFont="sans" compact dimmed selected trailing="" />
      </List>

      <SectionHeading title="Settings rows" dense />
      <SettingsSection title="Group">
        <SwitchRow label="Conversation previews" checked={on} onChange={setOn} />
        <PickerRow
          label="Density"
          value={density}
          onChange={setDensity}
          options={[
            { value: "summary", label: "Summary" },
            { value: "full", label: "Full" },
          ]}
        />
        <SettingsRow label="Host" detail={<span className="mono">127.0.0.1:8787</span>} />
        <LinkRow label="Appearance" detail="Dark" href="/gallery" />
        <SettingsRow label="Folder" detail="~/waypoint" mono onPress={() => {}} />
      </SettingsSection>

      <SectionHeading title="Choice field" dense />
      <ChoiceField
        label="Choice"
        value={choice}
        onChange={setChoice}
        options={[
          { value: "", label: "Default" },
          { value: "high", label: "High", detail: "More thinking per turn" },
          { value: "low", label: "Low" },
        ]}
      />

      <SectionHeading title="File thumbnails" dense />
      <div className={styles.row}>
        <FileThumb src="/favicon.svg" />
        <FileThumb src={null} />
      </div>

      <SectionHeading title="File names" dense />
      <div className={styles.fields}>
        <FileName name="quarterly-capacity-planning-spreadsheet-final-v3.xlsx" />
      </div>

      <SectionHeading title="Fields" dense />
      <div className={styles.fields}>
        <SearchInput aria-label="Search sessions" placeholder="Search sessions" />
        <Field label="Host" htmlFor="g-host">
          <TextInput id="g-host" mono defaultValue="10.0.4.12" />
        </Field>
        <Field label="Folder" htmlFor="g-folder">
          <TailInput id="g-folder" mono defaultValue="/home/ada/Workspace/app-name/docs/architecture" />
        </Field>
        <Field label="Password" htmlFor="g-pass">
          <PasswordInput id="g-pass" defaultValue="hunter2" />
        </Field>
        <Field label="Effort" htmlFor="g-effort">
          <Select id="g-effort" value={effort} onChange={(event) => setEffort(event.target.value)}>
            <option value="low">Low</option>
            <option value="high">High</option>
          </Select>
        </Field>
        <Field label="Message" htmlFor="g-message">
          <TextArea id="g-message" rows={3} defaultValue="Run the tests again." />
        </Field>
      </div>

      <SectionHeading title="Overlays" />
      <div className={styles.row}>
        <Button onClick={() => setSheet(true)}>Sheet</Button>
        <Button onClick={() => setModal(true)}>Modal</Button>
        <Button onClick={() => setWide(true)}>Wide modal</Button>
        <Button onClick={() => setConfirm(true)}>Confirm</Button>
        <Button ref={trigger} onClick={() => setPopover((open) => !open)}>
          Popover
        </Button>
        <Button ref={flyoutTrigger} onClick={() => setFlyout((open) => !open)}>
          Flyout
        </Button>
        <Button onClick={() => toast({ message: "Switched to studio" })}>Toast</Button>
        <Button
          onClick={() =>
            toast({ message: "Could not reach 10.0.4.12", tone: "error", action: { label: "Retry", onSelect: () => {} } })
          }
        >
          Error toast
        </Button>
      </div>

      <SectionHeading title="Glass bar" />
      <div className={styles.glassDemo}>
        <GlassBar>
          <IconButton label="Back">
            <Icon name="chevron-left" size={22} />
          </IconButton>
          <span className={styles.barTitle}>Fix held-message race</span>
          <IconButton label="More">
            <Icon name="more" />
          </IconButton>
        </GlassBar>
        <div className={styles.scrolling}>
          {Array.from({ length: 6 }, (_, i) => (
            <p key={i}>Content scrolls under the floating bar.</p>
          ))}
        </div>
      </div>

      <EmptyState title="Not built yet" detail="An empty body" />

      <Sheet
        open={sheet}
        onClose={() => setSheet(false)}
        title="Session"
        footer={<Button variant="primary" block onClick={() => setSheet(false)}>Done</Button>}
      >
        <List>
          <ListRow titleFont="sans" title="Switch session" trailing={<Icon name="chevron-right" size={14} />} />
          <ListRow titleFont="sans" title="Rename" trailing={<Icon name="chevron-right" size={14} />} />
          <ListRow titleFont="sans" title="Schedule message" trailing={<Icon name="chevron-right" size={14} />} />
        </List>
      </Sheet>
      <Modal
        open={modal}
        onClose={() => setModal(false)}
        title="Connect to gpu-box"
        footer={
          <>
            <Button variant="text" onClick={() => setModal(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => setModal(false)}>
              Connect
            </Button>
          </>
        }
      >
        <Field label="SSH password" htmlFor="g-ssh">
          <PasswordInput id="g-ssh" />
        </Field>
      </Modal>
      <Modal
        open={wide}
        onClose={() => setWide(false)}
        title="Switch session"
        subtitle="Claude Code, Opus 4.8"
        size="lg"
        footer={
          <Button variant="primary" onClick={() => setWide(false)}>
            Close
          </Button>
        }
      >
        <p>A wide modal with a subtitle under its title.</p>
      </Modal>
      <ConfirmDialog
        open={confirm}
        title="Terminate this session?"
        detail="The agent stops and the session moves to Exited."
        confirmLabel="Terminate"
        onConfirm={() => {}}
        onClose={() => setConfirm(false)}
      />
      <Popover open={popover} onOpenChange={setPopover} triggerRef={trigger} label="Actions" width={240}>
        <Menu label="Actions">
          <MenuItem onSelect={() => setPopover(false)} selected>
            Summary
          </MenuItem>
          <MenuItem onSelect={() => setPopover(false)}>Full</MenuItem>
          <MenuItem onSelect={() => setPopover(false)} tone="danger">
            Terminate
          </MenuItem>
        </Menu>
      </Popover>
      <Flyout
        open={flyout}
        onClose={() => setFlyout(false)}
        triggerRef={flyoutTrigger}
        title="Focus"
        side="top"
        width={280}
        resizeKey="waypoint.gallery-popover-size"
      >
        <p className={styles.flyoutDemo}>A resizable popover above its trigger on a pointer client, a sheet on touch.</p>
      </Flyout>
    </div>
  );
}
