// A text field holding a path or command reads from its end: a browser leaves one scrolled to its
// start when it is mounted, focused from code, or blurred.

export interface TailField {
  value: string;
  scrollLeft: number;
  scrollWidth: number;
  selectionStart: number | null;
  selectionEnd: number | null;
  setSelectionRange(start: number, end: number): void;
}

export interface TailEnv<F extends TailField> {
  isFocused(field: F): boolean;
  nextFrame(callback: () => void): void;
}

function showEnd(field: TailField): void {
  field.scrollLeft = field.scrollWidth;
}

// While focused only with the caret at the end, so an edit in the middle is not scrolled away from.
function followsEnd(field: TailField, focused: boolean): boolean {
  return !focused || (field.selectionStart === field.value.length && field.selectionEnd === field.value.length);
}

// The caret goes to the end of the value unless something is selected (a Tab into the field).
function caretToEnd(field: TailField): void {
  if (field.selectionStart === field.selectionEnd) {
    const end = field.value.length;
    field.setSelectionRange(end, end);
  }
  showEnd(field);
}

// A press leaves the caret where the person put it; a focus no press caused moves it to the end.
export function createTailAnchor<F extends TailField>({ isFocused, nextFrame }: TailEnv<F>) {
  let pressed = false;
  return {
    // After the field mounts or its value changes.
    sync(field: F | null): void {
      if (field && followsEnd(field, isFocused(field))) showEnd(field);
    },
    press(): void {
      pressed = true;
    },
    focus(field: F): void {
      if (pressed) return;
      caretToEnd(field);
      // Some browsers reset the scroll as the focus settles.
      nextFrame(() => showEnd(field));
    },
    blur(field: F): void {
      pressed = false;
      nextFrame(() => {
        if (!isFocused(field)) showEnd(field);
      });
    },
  };
}
