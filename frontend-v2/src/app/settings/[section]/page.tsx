import type { Metadata } from "next";

import { SettingsScreen } from "@/components/settings/SettingsScreen";

export const metadata: Metadata = { title: "Settings" };

interface Props {
  params: Promise<{ section: string }>;
}

export default async function Page({ params }: Props) {
  const { section } = await params;
  return <SettingsScreen sectionId={section} />;
}
