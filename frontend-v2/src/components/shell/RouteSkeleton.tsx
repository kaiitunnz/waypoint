import type { ReactNode } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

interface RouteSkeletonProps {
  title: string;
  // A mono machine string the route was opened with (an id, a channel).
  subject?: string | null;
  titleExtra?: ReactNode;
  back?: { href: string; label: string };
}

// Placeholder for a route whose screen is not built yet.
export function RouteSkeleton({ title, subject, titleExtra, back }: RouteSkeletonProps) {
  return (
    <>
      <PageHeader title={title} titleExtra={titleExtra} back={back} />
      <EmptyState
        title="Not built yet"
        detail={subject ? <span className="mono">{subject}</span> : undefined}
      />
    </>
  );
}
