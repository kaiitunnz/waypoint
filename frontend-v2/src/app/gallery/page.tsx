import type { Metadata } from "next";

import { Gallery } from "@/components/gallery/Gallery";

export const metadata: Metadata = { title: "Gallery", robots: { index: false, follow: false } };

export default function Page() {
  return <Gallery />;
}
