import { describe, expect, it } from "vitest";

import {
  isPhoneLayout,
  MULTI_COLUMN_QUERY,
  PANE_AUTO_QUERY,
  RAIL_AUTO_QUERY,
  sidePanelMode,
} from "./layoutRules";

const minWidth = (query: string) => Number(/min-width: (\d+)px/.exec(query)?.[1]);

describe("sidePanelMode", () => {
  it("puts the panels beside the conversation wherever there is room, for either input", () => {
    expect(sidePanelMode(true, true)).toBe("columns");
    expect(sidePanelMode(false, true)).toBe("columns");
  });

  it("draws them over a narrow pointer window", () => {
    expect(sidePanelMode(true, false)).toBe("drawers");
  });

  it("leaves them out of a narrow touch screen", () => {
    expect(sidePanelMode(false, false)).toBe("none");
  });
});

describe("isPhoneLayout", () => {
  it("is only a touch screen with room for one column", () => {
    expect(isPhoneLayout(false, false)).toBe(true);
    expect(isPhoneLayout(true, false)).toBe(false);
    expect(isPhoneLayout(false, true)).toBe(false);
    expect(isPhoneLayout(true, true)).toBe(false);
  });
});

describe("breakpoints", () => {
  it("opens the rail before the side pane, both above the one-column width", () => {
    expect(minWidth(MULTI_COLUMN_QUERY)).toBeLessThan(minWidth(RAIL_AUTO_QUERY));
    expect(minWidth(RAIL_AUTO_QUERY)).toBeLessThan(minWidth(PANE_AUTO_QUERY));
  });
});
