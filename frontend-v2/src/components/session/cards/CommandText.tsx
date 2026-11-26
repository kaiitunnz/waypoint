"use client";

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

// A command or script in mono, folded at eight lines.
export function CommandText({ text }: { text: string }) {
  const { open, toggle, shown, total, content, chip } = useFold<HTMLDivElement>(text, "start");
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
