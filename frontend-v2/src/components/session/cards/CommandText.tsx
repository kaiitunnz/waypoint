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

// A command or script in mono, folded at eight lines unless `whole`.
export function CommandText({ text, whole }: { text: string; whole?: boolean }) {
  return whole ? (
    <div className={styles.mono}>
      <Lines text={foldLines(text, Infinity).text} />
    </div>
  ) : (
    <FoldedCommand text={text} />
  );
}

function FoldedCommand({ text }: { text: string }) {
  const { open, toggle, shown, total, content, chip } = useFold<HTMLDivElement>(text);
  return (
    <>
      <div className={cx(styles.mono, !open && clamp.clamp)}>
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
