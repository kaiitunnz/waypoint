import type { Step } from "@waypoint/web-core/activity";

import { InlineCode } from "@/components/ui/InlineCode";

export function StepText({ step }: { step: Step }) {
  return step.subject ? (
    <>
      {step.verb} <InlineCode>{step.subject}</InlineCode>
    </>
  ) : (
    step.verb
  );
}
