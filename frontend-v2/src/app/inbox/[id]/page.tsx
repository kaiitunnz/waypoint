import type { Metadata } from "next";

import { decodeNeedsId } from "@waypoint/web-core/needsYou";

import { ItemDetail } from "@/components/needs-you/ItemDetail";

interface Props {
  params: Promise<{ id: string }>;
}

export const metadata: Metadata = { title: "Needs you" };

export default async function Page({ params }: Props) {
  const { id } = await params;
  const decoded = decodeNeedsId(id);
  // Keyed by id so moving between items starts a fresh view.
  return <ItemDetail key={decoded} id={decoded} />;
}
