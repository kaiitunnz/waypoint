import { describe, expect, it } from "vitest";

import { CONVERSATION_PREVIEWS_DEFAULT, CONVERSATION_PREVIEWS_KEY } from "./useConversationPreviews";

describe("conversation previews", () => {
  it("are on until turned off, under their own key", () => {
    expect(CONVERSATION_PREVIEWS_KEY).toBe("waypoint.conversation-previews");
    expect(CONVERSATION_PREVIEWS_DEFAULT).toBe(true);
  });
});
