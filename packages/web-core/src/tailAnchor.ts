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

// Whether a changed value should scroll to its end: always while unfocused, and while focused only
// with the caret at the end (a suggestion filling the field, or typing at the end), so an edit in
// the middle is not scrolled away from.
export function followsEnd(field: TailField, focused: boolean): boolean {
  return !focused || (field.selectionStart === field.value.length && field.selectionEnd === field.value.length);
}

// For a focus that no press caused: the caret goes to the end of the value unless something is
// already selected (a Tab into the field).
export function caretToEnd(field: TailField): void {
  if (field.selectionStart === field.selectionEnd) {
    const end = field.value.length;
    field.setSelectionRange(end, end);
  }
  showEnd(field);
}
