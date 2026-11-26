// A drag that started in this document, e.g. a transcript image, which Chrome
// types as "Files".
let internalDrag = false;

export function isExternalFileDrag(data: DataTransfer | null | undefined): data is DataTransfer {
  return !internalDrag && !!data && data.types.includes("Files");
}

// Cancels file drops nobody accepted, which the browser would open in the tab.
// Window listeners run after React's root listeners, so a drop a zone accepted
// (`defaultPrevented`) keeps its drop effect. Returns the uninstall function.
export function installFileDropGuard(
  target: Pick<Window, "addEventListener" | "removeEventListener"> = window,
): () => void {
  const clearInternal = () => {
    internalDrag = false;
  };
  const onDragStart = () => {
    internalDrag = true;
  };
  const onDragOver = (event: DragEvent) => {
    if (event.defaultPrevented || !isExternalFileDrag(event.dataTransfer)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "none";
  };
  const onDrop = (event: DragEvent) => {
    if (isExternalFileDrag(event.dataTransfer)) event.preventDefault();
    internalDrag = false;
  };
  target.addEventListener("dragstart", onDragStart);
  target.addEventListener("dragend", clearInternal);
  // A drag source removed mid-drag never delivers `dragend` to the window.
  target.addEventListener("pointerdown", clearInternal);
  target.addEventListener("dragover", onDragOver as EventListener);
  target.addEventListener("drop", onDrop as EventListener);
  return () => {
    target.removeEventListener("dragstart", onDragStart);
    target.removeEventListener("dragend", clearInternal);
    target.removeEventListener("pointerdown", clearInternal);
    target.removeEventListener("dragover", onDragOver as EventListener);
    target.removeEventListener("drop", onDrop as EventListener);
  };
}

// The files of a paste or drop, preferring the `items` API (which carries pasted
// screenshots) and falling back to `files`.
export function filesFromDataTransfer(data: DataTransfer | null): File[] {
  if (!data) return [];
  const out: File[] = [];
  if (data.items && data.items.length > 0) {
    for (const item of Array.from(data.items)) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) out.push(file);
      }
    }
  } else if (data.files && data.files.length > 0) {
    out.push(...Array.from(data.files));
  }
  return out;
}

// Whether a paste attaches its files rather than pasting its text. A file manager
// copies a file with its name, path or URI as text; an office app puts a picture
// of the selection beside text that names no file.
export function pasteAttaches(files: readonly File[], text: string): boolean {
  if (files.length === 0) return false;
  const names = new Set(files.map((file) => file.name.normalize("NFC")));
  return text.split(/[\r\n]+/).every((line) => !line.trim() || names.has(namedBy(line.trim()).normalize("NFC")));
}

// The file name a clipboard line stands for: its own, or the last part of an
// absolute path or file URI. A web address or relative path is just text.
function namedBy(line: string): string {
  const uri = line.startsWith("file://");
  if (!uri && !line.startsWith("/")) return line;
  const name = line.slice(line.lastIndexOf("/") + 1);
  if (!uri) return name;
  try {
    return decodeURIComponent(name);
  } catch {
    return name;
  }
}
