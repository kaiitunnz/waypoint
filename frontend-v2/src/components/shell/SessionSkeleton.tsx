import Link from "next/link";

import { EmptyState } from "@/components/ui/EmptyState";
import { GlassBar } from "@/components/ui/GlassBar";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";

import styles from "./SessionSkeleton.module.css";

export function SessionSkeleton({ id, view, subject }: { id: string; view: "chat" | "files"; subject?: string }) {
  const title = view === "files" ? "Files" : "Session";
  return (
    <div className={styles.screen}>
      <GlassBar>
        <Link href={view === "files" ? `/session/${encodeURIComponent(id)}` : "/"} className={styles.back} aria-label="Back">
          <Icon name="chevron-left" size={22} />
        </Link>
        <div className={styles.titles}>
          <span className={styles.title}>{title}</span>
          <span className={`${styles.id} mono`}>{id}</span>
        </div>
        <IconButton label="Session actions" disabled>
          <Icon name="more" size={20} />
        </IconButton>
      </GlassBar>
      <div className={styles.body}>
        <EmptyState
          title="Not built yet"
          detail={subject ? <span className="mono">{subject}</span> : undefined}
        />
      </div>
    </div>
  );
}
