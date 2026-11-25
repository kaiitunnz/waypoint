import type { ReactNode } from "react";

import { SessionPresence } from "@/components/shell/SessionPresence";

export default async function SessionLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <>
      <SessionPresence id={id} />
      {children}
    </>
  );
}
