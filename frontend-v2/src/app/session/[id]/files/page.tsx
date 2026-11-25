import type { Metadata } from "next";

import { FilesSkeleton } from "@/components/shell/FilesSkeleton";

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
  return <FilesSkeleton id={id} subject={path} />;
}
