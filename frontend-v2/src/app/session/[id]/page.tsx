import type { Metadata } from "next";

import { SessionScreen } from "@/components/session/SessionScreen";

interface Props {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  return { title: `Session ${id}` };
}

export default async function Page({ params }: Props) {
  const { id } = await params;
  // Keyed by id so moving between sessions starts a fresh stream.
  return <SessionScreen key={id} id={id} />;
}
