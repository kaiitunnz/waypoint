import type { ReactNode } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

interface RouteSkeletonProps {
  title: string;
  // A mono machine string the route was opened with (an id, a channel).
  subject?: string | null;
  titleExtra?: ReactNode;
  actions?: ReactNode;
  back?: { href: string; label: string };
}

// The placeholder every route renders until its screen is built: the page
// header and an honest empty body.
export function RouteSkeleton({ title, subject, titleExtra, actions, back }: RouteSkeletonProps) {
  return (
    <>
      <PageHeader title={title} titleExtra={titleExtra} actions={actions} back={back} />
      <EmptyState
        title="Not built yet"
        detail={subject ? <span className="mono">{subject}</span> : undefined}
      />
    </>
  );
}
