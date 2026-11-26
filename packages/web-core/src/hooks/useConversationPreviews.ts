"use client";

import { useStoredFlag } from "./useStoredFlag";

export const CONVERSATION_PREVIEWS_KEY = "waypoint.conversation-previews";
export const CONVERSATION_PREVIEWS_DEFAULT = true;

// Whether session lists show each conversation's latest line.
export function useConversationPreviews(): [boolean, (show: boolean) => void] {
  return useStoredFlag(CONVERSATION_PREVIEWS_KEY, CONVERSATION_PREVIEWS_DEFAULT);
}
