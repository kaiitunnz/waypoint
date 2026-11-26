import { afterEach, describe, expect, it } from "vitest";

import { filesFromDataTransfer, installFileDropGuard, isExternalFileDrag, pasteAttaches } from "./fileDrop";

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

describe("pasteAttaches", () => {
  const files = (...names: string[]) => names.map((name) => ({ name }) as File);

  it("attaches a plain image paste, which carries no text", () => {
    expect(pasteAttaches(files("image.png"), "")).toBe(true);
    expect(pasteAttaches(files("image.png"), "\n")).toBe(true);
  });

  it("attaches files copied in Finder, named one per line", () => {
    expect(pasteAttaches(files("report.pdf"), "report.pdf")).toBe(true);
    expect(pasteAttaches(files("a.txt", "b 2.txt"), "a.txt\rb 2.txt")).toBe(true);
    // A decomposed name in the text, a composed one on the file.
    expect(pasteAttaches(files("\u00e9.txt"), "e\u0301.txt")).toBe(true);
    expect(pasteAttaches(files("100%.txt"), "100%.txt")).toBe(true);
  });

  it("attaches files copied in Nautilus or Dolphin, given as paths or URIs", () => {
    expect(pasteAttaches(files("a.txt"), "/home/me/docs/a.txt\n")).toBe(true);
    expect(pasteAttaches(files("a b.txt", "c.png"), "file:///home/me/a%20b.txt\r\nfile:///home/me/c.png")).toBe(true);
    expect(pasteAttaches(files("50%.txt"), "file:///home/me/50%.txt")).toBe(true);
    // Only a URI is encoded.
    expect(pasteAttaches(files("a%20b.txt"), "/home/me/a%20b.txt")).toBe(true);
  });

  it("lets an office app's text paste, picture and all", () => {
    expect(pasteAttaches(files("image.png"), "Q1\tQ2\n10\t12")).toBe(false);
    expect(pasteAttaches(files("image.png"), "A heading")).toBe(false);
  });

  it("lets text paste when a line names no file, or there is none", () => {
    expect(pasteAttaches(files("a.txt"), "a.txt\nand a note")).toBe(false);
    expect(pasteAttaches([], "a.txt")).toBe(false);
    expect(pasteAttaches([], "")).toBe(false);
  });
});
