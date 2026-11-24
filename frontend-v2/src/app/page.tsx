import type { Metadata } from "next";

import { HostSwitcher } from "@/components/hosts/HostSwitcher";
import { RouteSkeleton } from "@/components/shell/RouteSkeleton";

// The root segment does not take the layout's title template.
export const metadata: Metadata = { title: { absolute: "Sessions · Waypoint" } };

export default function Page() {
  return <RouteSkeleton title="Sessions" titleExtra={<HostSwitcher />} />;
}
