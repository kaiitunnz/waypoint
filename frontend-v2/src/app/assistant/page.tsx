import type { Metadata } from "next";

import { RouteSkeleton } from "@/components/shell/RouteSkeleton";

export const metadata: Metadata = { title: "Assistant" };

export default function Page() {
  return <RouteSkeleton title="Assistant" back={{ href: "/", label: "Sessions" }} />;
}
