// Whether a transcript follows its end, and how a fold the person toggles holds it still.

// A scroll that lands this close to the end resumes following.
export const FOLLOW_PX = 160;

export interface Follow {
  following: boolean;
  // Set while a toggled fold holds the view: where the view was last put, and whether
  // a message the person sends should still bring them to the end.
  hold: { scrollTop: number; resumeOnSend: boolean } | null;
}

export const FOLLOWING: Follow = { following: true, hold: null };

// A fold was toggled and has yet to lay out.
export function toggled(state: Follow, scrollTop: number): Follow {
  return { following: false, hold: { scrollTop, resumeOnSend: state.following || (state.hold?.resumeOnSend ?? false) } };
}

// The toggled fold has laid out. Closing one that leaves the view at the end follows again.
export function settled(resumeOnSend: boolean, closing: boolean, scrollTop: number, fromEnd: number): Follow {
  if (closing && fromEnd <= 1) return FOLLOWING;
  return { following: false, hold: { scrollTop, resumeOnSend } };
}

export function scrolled(state: Follow, scrollTop: number, fromEnd: number): Follow {
  // A scroll that leaves the held view where it was put is not the person's.
  if (state.hold?.scrollTop === scrollTop) return state;
  return { following: fromEnd < FOLLOW_PX, hold: null };
}

// The transcript moved the view to keep its place.
export function moved(state: Follow, scrollTop: number): Follow {
  return state.hold ? { ...state, hold: { ...state.hold, scrollTop } } : state;
}

export function sent(state: Follow): Follow {
  return state.hold?.resumeOnSend ? FOLLOWING : state;
}
