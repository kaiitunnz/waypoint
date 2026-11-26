import type { Metadata } from "next";

import { SessionScreen } from "@/components/session/SessionScreen";

interface Props {
  params: Promise<{ id: string }>;
}

interface PageProps extends Props {
  searchParams: Promise<{ view?: string | string[] }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  return { title: `Session ${id}` };
}

export default async function Page({ params, searchParams }: PageProps) {
  const { id } = await params;
  const { view } = await searchParams;
  // Keyed by id so moving between sessions starts a fresh stream.
  return <SessionScreen key={id} id={id} initialView={view === "terminal" ? "terminal" : "chat"} />;
}
