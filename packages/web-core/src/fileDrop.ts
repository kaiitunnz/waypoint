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
