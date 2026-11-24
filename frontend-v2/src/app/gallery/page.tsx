import type { Metadata } from "next";

import { Gallery } from "@/components/shell/Gallery";

export const metadata: Metadata = { title: "Gallery", robots: { index: false, follow: false } };

// An internal page that shows every primitive. It has no navigation entry.
export default function Page() {
  return <Gallery />;
}
