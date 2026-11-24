import type { Metadata } from "next";

import { RouteSkeleton } from "@/components/shell/RouteSkeleton";

interface Props {
  params: Promise<{ id: string }>;
}

export const metadata: Metadata = { title: "Needs you" };

export default async function Page({ params }: Props) {
  const { id } = await params;
  return <RouteSkeleton title="Needs you" subject={id} back={{ href: "/inbox", label: "Needs you" }} />;
}
