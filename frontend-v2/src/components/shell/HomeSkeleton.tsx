"use client";

import { RouteSkeleton } from "./RouteSkeleton";
import { HostSwitcher } from "./HostSwitcher";

export function HomeSkeleton() {
  return <RouteSkeleton title="Sessions" titleExtra={<HostSwitcher />} />;
}
