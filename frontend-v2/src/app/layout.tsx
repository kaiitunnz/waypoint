import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Source_Sans_3, Source_Serif_4 } from "next/font/google";

import { Providers } from "@/components/shell/Providers";
import { ANTI_FLASH_SCRIPT } from "@/lib/theme-script";

import "@/styles/tokens.css";
import "@/styles/base.css";

// Serif names things, sans carries UI and prose, mono is for machine strings only.
const serif = Source_Serif_4({
  subsets: ["latin"],
  axes: ["opsz"],
  weight: "variable",
  variable: "--font-serif",
  display: "swap",
});

const sans = Source_Sans_3({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: { template: "%s · Waypoint", default: "Waypoint" },
  description: "Remote control for coding-agent sessions",
  appleWebApp: { capable: true, statusBarStyle: "black-translucent", title: "Waypoint" },
};

// viewport-fit=cover lets the page run under the status bar and home indicator;
// the chrome pads itself by env(safe-area-inset-*). The theme-color meta is
// managed by the inline script below, so it is not declared here.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${serif.variable} ${sans.variable} ${mono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
        <script dangerouslySetInnerHTML={{ __html: ANTI_FLASH_SCRIPT }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
