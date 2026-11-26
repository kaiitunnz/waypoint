import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { settingsSection } from "@/components/settings/sections";
import { SettingsScreen } from "@/components/settings/SettingsScreen";

interface Props {
  params: Promise<{ section: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { section } = await params;
  return { title: settingsSection(section)?.title ?? "Settings" };
}

export default async function Page({ params }: Props) {
  const { section } = await params;
  if (!settingsSection(section)) notFound();
  return <SettingsScreen sectionId={section} />;
}
