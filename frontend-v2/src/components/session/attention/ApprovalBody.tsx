"use client";

import type { ApprovalBody as ApprovalBodyModel } from "@waypoint/web-core/session/approvals";
import { relativeToCwd, splitPath } from "@waypoint/web-core/workspacePaths";

import { Clamped } from "../cards/Clamped";
import { CommandText } from "../cards/CommandText";
import { DiffBlock } from "../cards/DiffBlock";
import { Markdown } from "../cards/Markdown";
import { PathLabel } from "../cards/PathLabel";
import { useSessionContext } from "../SessionContext";
import styles from "./ApprovalBody.module.css";

// The prompt line is the card's title, so the body starts with the content.
export function ApprovalBody({ body }: { body: ApprovalBodyModel }) {
  const { cwd } = useSessionContext();
  switch (body.kind) {
    case "diff":
      return <DiffBlock preview={body.preview} headers />;
    case "plan":
    case "task":
      return (
        <Clamped lines={8} className={styles.markdown}>
          <Markdown text={body.kind === "plan" ? body.plan : body.prompt} />
        </Clamped>
      );
    case "command":
    case "workflow":
    case "raw":
      return (
        <div className={styles.block}>
          <CommandText text={body.kind === "command" ? body.command : body.kind === "workflow" ? body.script : body.text} />
        </div>
      );
    case "edit":
      return (
        <>
          {body.path ? <PathLabel {...splitPath(relativeToCwd(body.path, cwd))} full={body.path} className={styles.path} /> : null}
          <p className={styles.note}>No diff preview</p>
        </>
      );
  }
}
