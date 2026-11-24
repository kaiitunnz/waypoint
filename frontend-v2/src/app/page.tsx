import type { Metadata } from "next";

import { SessionsHome } from "@/components/sessions/SessionsHome";

// The root segment does not take the layout's title template.
export const metadata: Metadata = { title: { absolute: "Sessions · Waypoint" } };

export default function Page() {
  return <SessionsHome />;
}
