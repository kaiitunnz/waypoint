import { describe, expect, it, vi } from "vitest";

import { ApiError, AuthError, fetchInboxItem, fetchManagers, fetchManagerState } from "../api";
import { loadGates } from "./useBoardGates";

vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  fetchInboxItem: vi.fn(),
  fetchManagers: vi.fn(),
  fetchManagerState: vi.fn(),
}));

const manager = (id: string, attention_count = 1) => ({ id, project: id, repo_dir: "/r", ticket_count: 1, attention_count });
const state = {
  config: null,
  tickets: [{ id: "t1", state: "spec_review", priority: "p1", inbox_item_id: "i1" }],
} as unknown as Awaited<ReturnType<typeof fetchManagerState>>;

describe("loadGates", () => {
  it("keeps the managers it can read, and a gate whose item is gone", async () => {
    vi.mocked(fetchManagers).mockResolvedValue([manager("a"), manager("b"), manager("idle", 0)]);
    vi.mocked(fetchManagerState).mockImplementation(async (_host, _token, id) => {
      if (id !== "a") throw new ApiError("boom", 500);
      return state;
    });
    vi.mocked(fetchInboxItem).mockRejectedValue(new ApiError("not found", 404));

    const read = await loadGates("h", "t");
    expect(read.gates).toMatchObject([{ managerId: "a", item: null }]);
    expect(read.failed).toEqual(["b"]);
    expect(fetchManagerState).not.toHaveBeenCalledWith("h", "t", "idle");
  });

  it("counts a manager unread when an item read fails, and throws an auth error", async () => {
    vi.mocked(fetchManagers).mockResolvedValue([manager("a")]);
    vi.mocked(fetchManagerState).mockResolvedValue(state);
    vi.mocked(fetchInboxItem).mockRejectedValue(new ApiError("boom", 500));
    expect(await loadGates("h", "t")).toEqual({ gates: [], failed: ["a"] });

    vi.mocked(fetchInboxItem).mockRejectedValue(new AuthError());
    await expect(loadGates("h", "t")).rejects.toBeInstanceOf(AuthError);
  });
});
