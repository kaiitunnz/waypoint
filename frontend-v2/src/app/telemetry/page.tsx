import type { Metadata } from "next";

import { RouteSkeleton } from "@/components/shell/RouteSkeleton";

export const metadata: Metadata = { title: "Usage" };

export default function Page() {
  return <RouteSkeleton title="Usage" />;
}
