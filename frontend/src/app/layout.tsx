import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Waypoint",
  description: "Remote control for Claude Code and Codex sessions",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Waypoint",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
