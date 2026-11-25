import type { Metadata } from "next";

import { SessionSkeleton } from "@/components/shell/SessionSkeleton";

interface Props {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  return { title: `Session ${id}` };
}

export default async function Page({ params }: Props) {
  const { id } = await params;
  return <SessionSkeleton id={id} view="chat" />;
}
