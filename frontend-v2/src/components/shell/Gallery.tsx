"use client";

import { useRef, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { Modal, Sheet } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Field, PasswordInput, SearchInput, TextInput } from "@/components/ui/Field";
import { GlassBar } from "@/components/ui/GlassBar";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { List, ListRow } from "@/components/ui/List";
import { Menu, MenuItem, Popover } from "@/components/ui/Popover";
import { SectionHeading } from "@/components/ui/SectionHeading";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { StatusLamp, type LampState } from "@/components/ui/StatusLamp";
import { useToast } from "@/components/ui/Toast";
import { Toggle } from "@/components/ui/Toggle";

import { PageHeader } from "@/components/ui/PageHeader";
import styles from "./Gallery.module.css";

const LAMPS: LampState[] = ["needs", "working", "done", "error", "idle", "exited"];

export function Gallery() {
  const { toast } = useToast();
  const [sheet, setSheet] = useState(false);
  const [modal, setModal] = useState(false);
  const [popover, setPopover] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const [segment, setSegment] = useState("all");
  const [view, setView] = useState("chat");
  const [on, setOn] = useState(true);
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
          label="View"
          value={view}
          onChange={setView}
          segments={[
            { value: "chat", label: <Icon name="sessions" size={18} />, ariaLabel: "Chat" },
            { value: "terminal", label: <Icon name="ssh" size={18} />, ariaLabel: "Terminal" },
          ]}
        />
      </div>

      <SectionHeading title="Toggle, lamps, badges" />
      <div className={styles.row}>
        <Toggle checked={on} onChange={setOn} label="Focus" />
        {LAMPS.map((state) => (
          <span key={state} className={styles.lamp}>
            <StatusLamp state={state} />
            {state}
          </span>
        ))}
        <Badge count={5} />
        <Badge count={120} />
      </div>

      <SectionHeading title="List rows" count={4} action="See all" />
      <List>
        <ListRow lamp="needs" title="Fix held-message race" status="Which fix should I ship?" trailing="09:16" />
        <ListRow lamp="working" title="Heatmap keyboard access" status="Running npm run lint" trailing="2m" />
        <ListRow lamp="idle" title="Paper figures" compact pinned trailing="Yesterday" />
        <ListRow lamp="exited" title="597 exited" titleFont="sans" compact dimmed selected trailing="" />
      </List>

      <SectionHeading title="Fields" />
      <div className={styles.fields}>
        <SearchInput aria-label="Search sessions" placeholder="Search sessions" />
        <Field label="Host" htmlFor="g-host">
          <TextInput id="g-host" mono defaultValue="10.0.4.12" />
        </Field>
        <Field label="Password" htmlFor="g-pass">
          <PasswordInput id="g-pass" defaultValue="hunter2" />
        </Field>
      </div>

      <SectionHeading title="Overlays" />
      <div className={styles.row}>
        <Button onClick={() => setSheet(true)}>Sheet</Button>
        <Button onClick={() => setModal(true)}>Modal</Button>
        <Button ref={trigger} onClick={() => setPopover((open) => !open)}>
          Popover
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
        size="sm"
      >
        <Field label="SSH password" htmlFor="g-ssh">
          <PasswordInput id="g-ssh" />
        </Field>
      </Modal>
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
    </div>
  );
}
