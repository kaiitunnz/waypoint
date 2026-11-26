import { ActionChip } from "@/components/ui/ActionChip";

interface DisclosureProps {
  open: boolean;
  // Lines in the whole content; left out when the content is clamped by height.
  total?: number;
  // What `total` counts.
  noun?: string;
  onToggle: () => void;
}

export function Disclosure({ open, total, noun = "lines", onToggle }: DisclosureProps) {
  return (
    <ActionChip icon={open ? "chevron-up" : "chevron-down"} aria-expanded={open} onClick={onToggle}>
      {open ? "Show less" : total === undefined ? "Show all" : `Show all ${total} ${noun}`}
    </ActionChip>
  );
}
