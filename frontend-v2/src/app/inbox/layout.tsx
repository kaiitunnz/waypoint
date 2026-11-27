import type { ReactNode } from "react";

import { NeedsYouScreen } from "@/components/needs-you/NeedsYouScreen";

// The queue lives here, so moving between items keeps it as it is.
export default function Layout({ children }: { children: ReactNode }) {
  return <NeedsYouScreen>{children}</NeedsYouScreen>;
}
