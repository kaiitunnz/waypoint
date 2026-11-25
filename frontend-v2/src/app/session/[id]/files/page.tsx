import type { Metadata } from "next";

import { SessionSkeleton } from "@/components/shell/SessionSkeleton";

interface Props {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ path?: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  return { title: `Files ${id}` };
}

export default async function Page({ params, searchParams }: Props) {
  const [{ id }, { path }] = await Promise.all([params, searchParams]);
  return <SessionSkeleton id={id} subject={path} />;
}
