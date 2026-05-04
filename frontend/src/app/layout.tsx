import type { Metadata, Viewport } from "next";

import { ThemeProvider } from "@/lib/theme";
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

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

// Runs synchronously before any paint so there is no flash of wrong theme.
// ThemeProvider then takes over and keeps the attribute in sync at runtime.
const antiFlashScript = `(function(){
  var t=localStorage.getItem('waypoint-theme');
  var d=document.documentElement;
  if(t==='light'||t==='dark'){d.dataset.theme=t;}
  else if(window.matchMedia('(prefers-color-scheme: light)').matches){d.dataset.theme='light';}
})();`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: antiFlashScript }} />
      </head>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
