"use client";

import { useMediaQuery } from "@waypoint/web-core/hooks/use-media-query";

// The one breakpoint between the mobile and desktop layouts; stylesheets repeat
// the number in `@media (min-width: 900px)`.
const DESKTOP_QUERY = "(min-width: 900px)";

export function useDesktop(): boolean {
  return useMediaQuery(DESKTOP_QUERY);
}
