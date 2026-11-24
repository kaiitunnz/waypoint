import type { Metadata } from "next";

import { RouteSkeleton } from "@/components/shell/RouteSkeleton";

export const metadata: Metadata = { title: "Board" };

interface Props {
  searchParams: Promise<{ channel?: string }>;
}

export default async function Page({ searchParams }: Props) {
  const { channel } = await searchParams;
  return <RouteSkeleton title="Board" subject={channel} />;
}
