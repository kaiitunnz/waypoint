"use client";

import { useRef, useState } from "react";

import { queueItemView, queueStripItem, type QueueItem } from "@waypoint/web-core/session/messageQueue";

import { Flyout } from "@/components/ui/Flyout";

import { QueueList, type QueueHandlers } from "./QueueList";
import { Panel } from "./Panel";
import { Strip } from "./Strip";

interface QueueDockProps extends QueueHandlers {
  items: QueueItem[];
  focus: boolean;
}

// The strip for the message queue: the next or latest message and a count.
export function QueueDock({ items, focus, ...handlers }: QueueDockProps) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const shown = queueStripItem(items);
  if (!shown) return null;
  const view = queueItemView(shown, { focus });
  const count = items.length;
  const title = "Message queue";

  return (
    <>
      <Strip
        ref={trigger}
        glyph={shown.kind === "held" ? "◎" : "◷"}
        label={view.text}
        meta={view.when}
        count={count > 1 ? count : undefined}
        ariaLabel={`${title}: ${count} waiting`}
        open={open}
        onToggle={() => setOpen(!open)}
      />
      <Flyout open={open} onClose={() => setOpen(false)} triggerRef={trigger} title={title} width={420} side="top" resizeKey="queue">
        <Panel title={title} count={count}>
          <QueueList items={items} focus={focus} {...handlers} />
        </Panel>
      </Flyout>
    </>
  );
}
