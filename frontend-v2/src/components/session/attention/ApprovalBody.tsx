"use client";

import type { ApprovalBody as ApprovalBodyModel } from "@waypoint/web-core/session/approvals";
import { relativeToCwd, splitPath } from "@waypoint/web-core/workspacePaths";

import { cx } from "@/lib/cx";

import { Clamped } from "../cards/Clamped";
import { CommandText } from "../cards/CommandText";
import { DiffBlock } from "../cards/DiffBlock";
import { Markdown } from "../cards/Markdown";
import { PathLabel } from "../cards/PathLabel";
import { useSessionContext } from "../SessionContext";
import styles from "./ApprovalBody.module.css";

// The prompt line is the card's title, so the body starts with the content. `whole`
// shows all of it, with nothing folded, cut or run off the side.
export function ApprovalBody({ body, whole }: { body: ApprovalBodyModel; whole?: boolean }) {
  const { cwd } = useSessionContext();
  switch (body.kind) {
    case "diff":
      return <DiffBlock preview={body.preview} headers whole={whole} />;
    case "plan":
    case "task": {
      const markdown = <Markdown text={body.kind === "plan" ? body.plan : body.prompt} />;
      return whole ? (
        <div className={cx(styles.markdown, styles.whole)}>{markdown}</div>
      ) : (
        <Clamped lines={8} className={styles.markdown}>
          {markdown}
        </Clamped>
      );
    }
    case "command":
    case "workflow":
    case "raw":
      return (
        <>
          {whole && body.kind === "command" && body.outsideSandbox ? (
            <p className={styles.warn}>Runs outside the sandbox</p>
          ) : null}
          <div className={styles.block}>
            <CommandText
              text={body.kind === "command" ? body.command : body.kind === "workflow" ? body.script : body.text}
              whole={whole}
            />
          </div>
        </>
      );
    case "edit":
      return (
        <>
          {body.path ? (
            <PathLabel {...splitPath(relativeToCwd(body.path, cwd))} full={body.path} className={styles.path} whole={whole} />
          ) : null}
          <p className={styles.note}>No diff preview</p>
        </>
      );
  }
}
