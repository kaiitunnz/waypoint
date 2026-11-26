"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { dismissSideQuestion, forkSideQuestion } from "@waypoint/web-core/api";
import { sideQuestionStripLabel, sortedSideQuestions } from "@waypoint/web-core/session/sideQuestions";
import type { SideQuestion } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { Flyout } from "@/components/ui/Flyout";
import { useFailureToast } from "@/lib/useFailureToast";

import { useSessionContext } from "../SessionContext";
import { SideQuestionList } from "./SideQuestionList";
import { Strip } from "./Strip";

interface SideQuestionDockProps {
  questions: Iterable<SideQuestion>;
  open: boolean;
  // False keeps the focus where it is, for a panel that opened by itself.
  autoFocus: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SideQuestionDock({ questions, open, autoFocus, onOpenChange }: SideQuestionDockProps) {
  const { host, token, sessionId } = useSessionContext();
  const router = useRouter();
  const fail = useFailureToast();
  const [dismissing, setDismissing] = useState<Set<string>>(new Set());
  const [forking, setForking] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const sorted = sortedSideQuestions(questions);
  const count = sorted.length;

  const dismiss = async (id: string) => {
    setDismissing((current) => new Set(current).add(id));
    try {
      await dismissSideQuestion(host, token, sessionId, id);
    } catch (error) {
      setDismissing((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
      fail(error, "Could not dismiss the side question");
    }
  };
  const fork = async (id: string) => {
    if (forking) return;
    setForking(id);
    try {
      const forked = await forkSideQuestion(host, token, sessionId, id);
      router.push(`/session/${forked.id}`);
    } catch (error) {
      setForking(null);
      fail(error, "Could not open the side question");
    }
  };

  if (count === 0) return null;
  const title = "Side questions";
  return (
    <>
      <Strip
        ref={trigger}
        glyph="¶"
        label={sideQuestionStripLabel(sorted)}
        count={count > 1 ? count : undefined}
        ariaLabel={`${title}: ${count}`}
        open={open}
        onToggle={() => onOpenChange(!open)}
      />
      <Flyout
        open={open}
        onClose={() => onOpenChange(false)}
        triggerRef={trigger}
        title={title}
        count={count}
        autoFocus={autoFocus}
        actions={
          count >= 2 ? (
            <Button variant="text" size="sm" onClick={() => sorted.forEach((aside) => void dismiss(aside.id))}>
              Clear all
            </Button>
          ) : undefined
        }
        width={420}
        side="top"
        resizeKey="side-questions"
      >
        <SideQuestionList questions={sorted} dismissing={dismissing} forking={forking} onDismiss={(id) => void dismiss(id)} onFork={(id) => void fork(id)} />
      </Flyout>
    </>
  );
}
