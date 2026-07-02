/* The assistant's identity mark: the conventional AI sparkle — a large
 * four-point star with a small companion — drawn as a path rather than a
 * font glyph so it renders with consistent weight at any size. Uses
 * currentColor and scales with the surrounding font-size. */
export function AssistantMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="currentColor"
        d="M11 5C11.72 9.64 14.36 12.28 19 13C14.36 13.72 11.72 16.36 11 21C10.28 16.36 7.64 13.72 3 13C7.64 12.28 10.28 9.64 11 5Z"
      />
      <path
        fill="currentColor"
        d="M18.5 2C18.82 4.03 19.97 5.18 22 5.5C19.97 5.82 18.82 6.97 18.5 9C18.18 6.97 17.03 5.82 15 5.5C17.03 5.18 18.18 4.03 18.5 2Z"
      />
    </svg>
  );
}
