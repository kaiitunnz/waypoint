import type { MetadataRoute } from "next";

import { THEME_COLORS } from "@/lib/theme-script";

const GROUND = THEME_COLORS.dark;

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Waypoint",
    short_name: "Waypoint",
    description: "Remote control for coding-agent sessions",
    start_url: "/",
    display: "standalone",
    background_color: GROUND,
    theme_color: GROUND,
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512-maskable.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
