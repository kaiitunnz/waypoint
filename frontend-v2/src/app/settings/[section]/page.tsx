import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SETTINGS_SECTIONS } from "@/components/settings/sections";
import { SettingsScreen } from "@/components/settings/SettingsScreen";

interface Props {
  params: Promise<{ section: string }>;
}

const sectionFor = (id: string) => SETTINGS_SECTIONS.find((section) => section.id === id);

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { section } = await params;
  return { title: sectionFor(section)?.title ?? "Settings" };
}

export default async function Page({ params }: Props) {
  const { section } = await params;
  if (!sectionFor(section)) notFound();
  return <SettingsScreen sectionId={section} />;
}
