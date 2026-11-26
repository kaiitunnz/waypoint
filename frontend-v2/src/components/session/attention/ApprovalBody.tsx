"use client";

import { useState } from "react";

import type { ApprovalBody as ApprovalBodyModel } from "@waypoint/web-core/session/approvals";
import { relativeToCwd, splitPath } from "@waypoint/web-core/workspacePaths";

import { cx } from "@/lib/cx";

import { CommandText } from "../cards/CommandText";
import { DiffBlock } from "../cards/DiffBlock";
import { Disclosure } from "../cards/Disclosure";
import { Markdown } from "../cards/Markdown";
import { PathLabel } from "../cards/PathLabel";
import { useClamp } from "../cards/useClamp";
import { useSessionContext } from "../SessionContext";
import styles from "./ApprovalBody.module.css";

// Long markdown (a plan, a subagent prompt) clamps with a disclosure.
export function ClampedMarkdown({ text }: { text: string }) {
  const [all, setAll] = useState(false);
  const { content, chip } = useClamp<HTMLDivElement>(all);
  return (
    <>
      <div className={cx(styles.markdown, !all && styles.clamped)}>
        <div ref={content}>
          <Markdown text={text} />
        </div>
      </div>
      <div ref={chip}>
        <Disclosure open={all} onToggle={() => setAll(!all)} />
      </div>
    </>
  );
}

// What the agent asks to do, by tool. The prompt line is the card's title.
export function ApprovalBody({ body }: { body: ApprovalBodyModel }) {
  const { cwd } = useSessionContext();
  switch (body.kind) {
    case "diff":
      return <DiffBlock preview={body.preview} headers />;
    case "plan":
      return <ClampedMarkdown text={body.plan} />;
    case "task":
      return <ClampedMarkdown text={body.prompt} />;
    case "command":
      return (
        <div className={styles.block}>
          <CommandText text={body.command} />
        </div>
      );
    case "workflow":
      return (
        <div className={styles.block}>
          <CommandText text={body.script} />
        </div>
      );
    case "edit": {
      const shown = body.path ? relativeToCwd(body.path, cwd) : null;
      const parts = shown ? splitPath(shown) : null;
      return (
        <>
          {parts && body.path ? <PathLabel dir={parts.dir} base={parts.base} full={body.path} className={styles.path} /> : null}
          <p className={styles.note}>No diff preview</p>
        </>
      );
    }
    case "raw":
      return (
        <div className={styles.block}>
          <CommandText text={body.text} />
        </div>
      );
  }
}
