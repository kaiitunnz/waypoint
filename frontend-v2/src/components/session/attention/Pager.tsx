import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";

import styles from "./Pager.module.css";

interface PagerProps {
  page: number;
  count: number;
  // What is paged, for the buttons' names.
  noun: string;
  disabled?: boolean;
  onStep: (direction: "prev" | "next") => void;
}

export function Pager({ page, count, noun, disabled = false, onStep }: PagerProps) {
  return (
    <span className={styles.pager}>
      <IconButton label={`Previous ${noun}`} size="sm" disabled={disabled || page === 0} onClick={() => onStep("prev")}>
        <Icon name="chevron-left" size={16} />
      </IconButton>
      <span className={styles.count}>
        {page + 1} / {count}
      </span>
      <IconButton label={`Next ${noun}`} size="sm" disabled={disabled || page === count - 1} onClick={() => onStep("next")}>
        <Icon name="chevron-right" size={16} />
      </IconButton>
    </span>
  );
}
