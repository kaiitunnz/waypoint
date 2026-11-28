// A text field holding a path or a command reads from its end: the last segment is the part
// that tells one value from another, and a browser leaves a field scrolled to its start when
// it is mounted, focused from code, or blurred.

export interface TailField {
  value: string;
  scrollLeft: number;
  scrollWidth: number;
  selectionStart: number | null;
  selectionEnd: number | null;
  setSelectionRange(start: number, end: number): void;
}

export function showEnd(field: TailField): void {
  field.scrollLeft = field.scrollWidth;
}

// For a focus that no press caused, such as a sheet focusing its field as it opens: the caret
// goes to the end of the value unless something is already selected (a Tab into the field).
export function caretToEnd(field: TailField): void {
  if (field.selectionStart === field.selectionEnd) {
    const end = field.value.length;
    field.setSelectionRange(end, end);
  }
  showEnd(field);
}
