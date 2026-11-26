import { ActionChip } from "@/components/ui/ActionChip";

interface DisclosureProps {
  open: boolean;
  // Lines in the whole content; left out when the content is clamped by height.
  total?: number;
  // What `total` counts.
  noun?: string;
  // The folded label, in place of "Show all".
  label?: string;
  onToggle: () => void;
}

export function Disclosure({ open, total, noun = "lines", label, onToggle }: DisclosureProps) {
  return (
    <ActionChip icon={open ? "chevron-up" : "chevron-down"} aria-expanded={open} onClick={onToggle}>
      {open ? "Show less" : (label ?? (total === undefined ? "Show all" : `Show all ${total} ${noun}`))}
    </ActionChip>
  );
}
