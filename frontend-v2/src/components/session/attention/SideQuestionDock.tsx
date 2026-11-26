"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { dismissSideQuestion, forkSideQuestion, isAuthError } from "@waypoint/web-core/api";
import { sideQuestionStripLabel, sortedSideQuestions } from "@waypoint/web-core/session/sideQuestions";
import type { SideQuestion } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { Flyout } from "@/components/ui/Flyout";
import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";

import { useSessionContext } from "../SessionContext";
import { Panel } from "./Panel";
import { SideQuestionList } from "./SideQuestionList";
import { Strip } from "./Strip";

interface SideQuestionDockProps {
  questions: Iterable<SideQuestion>;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SideQuestionDock({ questions, open, onOpenChange }: SideQuestionDockProps) {
  const { host, token, sessionId } = useSessionContext();
  const router = useRouter();
  const { toast } = useToast();
  const [dismissing, setDismissing] = useState<Set<string>>(new Set());
  const [forking, setForking] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const sorted = sortedSideQuestions(questions);
  const count = sorted.length;

  const fail = (error: unknown, fallback: string) => {
    if (isAuthError(error)) handleAuthFailure();
    else toast({ message: error instanceof Error ? error.message : fallback, tone: "error" });
  };
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
      <Flyout open={open} onClose={() => onOpenChange(false)} triggerRef={trigger} title={title} width={420} side="top" resizeKey="side-questions">
        <Panel
          title={title}
          count={count}
          actions={
            count >= 2 ? (
              <Button variant="text" size="sm" onClick={() => sorted.forEach((aside) => void dismiss(aside.id))}>
                Clear all
              </Button>
            ) : undefined
          }
        >
          <SideQuestionList questions={sorted} dismissing={dismissing} forking={forking} onDismiss={(id) => void dismiss(id)} onFork={(id) => void fork(id)} />
        </Panel>
      </Flyout>
    </>
  );
}
