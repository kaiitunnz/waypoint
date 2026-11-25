import { ActionChip } from "@/components/ui/ActionChip";

interface DisclosureProps {
  open: boolean;
  // Lines in the whole content.
  total: number;
  onToggle: () => void;
}

export function Disclosure({ open, total, onToggle }: DisclosureProps) {
  return (
    <ActionChip icon={open ? "chevron-up" : "chevron-down"} aria-expanded={open} onClick={onToggle}>
      {open ? "Show less" : `Show all ${total} lines`}
    </ActionChip>
  );
}
