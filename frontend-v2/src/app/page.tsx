import type { Metadata } from "next";

import { HomeSkeleton } from "@/components/shell/HomeSkeleton";

// The root segment does not take the layout's title template.
export const metadata: Metadata = { title: { absolute: "Sessions · Waypoint" } };

export default function Page() {
  return <HomeSkeleton />;
}
