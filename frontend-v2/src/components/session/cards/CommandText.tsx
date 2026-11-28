"use client";

import { foldLines } from "@waypoint/web-core/session/foldText";

import { cx } from "@/lib/cx";

import clamp from "./Clamp.module.css";
import styles from "./CommandText.module.css";
import { Disclosure } from "./Disclosure";
import { useFold } from "./useFold";

// Each line of a text is its own block, so a wrapped line hangs under its own start.
export function Lines({ text }: { text: string }) {
  return text.split("\n").map((line, index) => (
    <span key={index} className={styles.ln}>
      {line || " "}
    </span>
  ));
}

// The mark before a shell command set under a heading; no part of the command.
export function ShellPrompt({ className }: { className?: string }) {
  return (
    <span className={cx(styles.shellPrompt, className)} aria-hidden="true">
      $
    </span>
  );
}

// A command clipped to one line, under a heading. `shell` marks a shell command.
export function CommandLine({ text, shell }: { text: string; shell: boolean }) {
  return (
    <span className={styles.line}>
      {shell ? (
        <>
          <ShellPrompt />{" "}
        </>
      ) : null}
      {text}
    </span>
  );
}

// A command or script in mono, folded at eight lines unless `whole`.
export function CommandText({ text, whole }: { text: string; whole?: boolean }) {
  return whole ? (
    <div className={styles.mono}>
      <Lines text={foldLines(text, Infinity).text} />
    </div>
  ) : (
    <FoldedCommand text={text} className={styles.mono} />
  );
}

function FoldedCommand({ text, className }: { text: string; className: string }) {
  const { open, toggle, shown, total, content, chip } = useFold<HTMLDivElement>(text);
  return (
    <>
      <div className={cx(className, !open && clamp.clamp)}>
        <div ref={content}>
          <Lines text={shown} />
        </div>
      </div>
      <div ref={chip}>
        <Disclosure open={open} total={total} onToggle={toggle} />
      </div>
    </>
  );
}
