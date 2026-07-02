/* The assistant's identity mark: a tilted compass needle — north half
 * filled, south hollow — inside a quiet ring. Draws in currentColor and
 * scales with the surrounding font-size. */
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
      <circle
        cx="12"
        cy="12"
        r="8.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        opacity="0.45"
      />
      <g transform="rotate(45 12 12)">
        <path d="M12 4.9 L14.6 12 L9.4 12 Z" fill="currentColor" />
        <path
          d="M9.4 12 L14.6 12 L12 19.1 Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
      </g>
    </svg>
  );
}
