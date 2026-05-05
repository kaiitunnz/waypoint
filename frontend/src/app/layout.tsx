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
// Also emits <meta name="theme-color"> in the resolved color so iOS Safari's
// rubber-band overscroll matches the page background on the very first frame —
// ThemeProvider can't do this without a hydration-time flash.
const antiFlashScript = `(function(){
  var theme='dark';
  try{
    var t=localStorage.getItem('waypoint-theme');
    if(t==='light'||t==='dark'){theme=t;}
    else if(window.matchMedia('(prefers-color-scheme: light)').matches){theme='light';}
  }catch(e){}
  document.documentElement.dataset.theme=theme;
  var m=document.createElement('meta');
  m.name='theme-color';
  m.content=theme==='light'?'#f4f1eb':'#06080b';
  document.head.appendChild(m);
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
