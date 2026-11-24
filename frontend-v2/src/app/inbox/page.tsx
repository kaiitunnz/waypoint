import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { RouteSkeleton } from "@/components/shell/RouteSkeleton";

export const metadata: Metadata = { title: "Needs you" };

interface Props {
  searchParams: Promise<{ item?: string }>;
}

// v1 links open an item as `/inbox?item=<id>`; an item is its own screen here.
export default async function Page({ searchParams }: Props) {
  const { item } = await searchParams;
  if (item) {
    redirect(`/inbox/${encodeURIComponent(item)}`);
  }
  return <RouteSkeleton title="Needs you" />;
}
