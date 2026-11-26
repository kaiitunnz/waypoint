import { createContext } from "react";

// Keeps the transcript's view still when something in it changes size without an
// aria-expanded control. Call it before the change.
export const TranscriptHoldContext = createContext<(element: Element, closing: boolean) => void>(() => {});
