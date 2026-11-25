import { afterEach, describe, expect, it } from "vitest";

import { filesFromDataTransfer, installFileDropGuard, isExternalFileDrag } from "./fileDrop";

function dragEvent(type: string, types: string[] = ["Files"]) {
  const event = new Event(type, { cancelable: true }) as Event & { dataTransfer: unknown };
  event.dataTransfer = { types, dropEffect: "copy" };
  return event as Event & { dataTransfer: { types: string[]; dropEffect: string } };
}

describe("installFileDropGuard", () => {
  let uninstall: (() => void) | null = null;
  afterEach(() => uninstall?.());

  function install() {
    const target = new EventTarget() as unknown as Window;
    uninstall = installFileDropGuard(target);
    return target;
  }

  it("cancels an external file drop nobody accepted", () => {
    const target = install();
    const over = dragEvent("dragover");
    target.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(true);
    expect(over.dataTransfer.dropEffect).toBe("none");
    const drop = dragEvent("drop");
    target.dispatchEvent(drop);
    expect(drop.defaultPrevented).toBe(true);
  });

  it("leaves a drag a zone already accepted alone", () => {
    const target = install();
    const over = dragEvent("dragover");
    over.preventDefault();
    target.dispatchEvent(over);
    expect(over.dataTransfer.dropEffect).toBe("copy");
  });

  it("ignores drags that carry no files", () => {
    const target = install();
    const over = dragEvent("dragover", ["text/plain"]);
    target.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(false);
  });

  it("treats a drag that began in the page as internal", () => {
    const target = install();
    target.dispatchEvent(new Event("dragstart"));
    expect(isExternalFileDrag({ types: ["Files"] } as unknown as DataTransfer)).toBe(false);
    const over = dragEvent("dragover");
    target.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(false);
    target.dispatchEvent(new Event("dragend"));
    expect(isExternalFileDrag({ types: ["Files"] } as unknown as DataTransfer)).toBe(true);
  });

  it("stops guarding once uninstalled", () => {
    const target = install();
    uninstall?.();
    uninstall = null;
    const over = dragEvent("dragover");
    target.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(false);
  });
});

describe("filesFromDataTransfer", () => {
  const file = { name: "a.txt" } as File;
  it("reads the items' files, skipping text", () => {
    const data = {
      items: [
        { kind: "string", getAsFile: () => null },
        { kind: "file", getAsFile: () => file },
      ],
      files: [],
    } as unknown as DataTransfer;
    expect(filesFromDataTransfer(data)).toEqual([file]);
  });
  it("falls back to the file list and ignores nothing to read", () => {
    expect(filesFromDataTransfer({ items: [], files: [file] } as unknown as DataTransfer)).toEqual([file]);
    expect(filesFromDataTransfer(null)).toEqual([]);
  });
});
