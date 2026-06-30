"use client";

import { forwardRef, InputHTMLAttributes, useState } from "react";

type PasswordInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type">;

// A password field with a trailing show/hide toggle. The icon reflects the
// current visibility: an open eye when the password is revealed, a struck-through
// eye when concealed. Forwards its ref and any input props to the underlying
// <input> so callers can manage focus, validation, and value as usual.
export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  function PasswordInput(props, ref) {
    const [revealed, setRevealed] = useState(false);
    return (
      <div className="input-reveal">
        <input ref={ref} type={revealed ? "text" : "password"} {...props} />
        <button
          type="button"
          className="input-reveal-toggle"
          aria-label={revealed ? "Hide password" : "Show password"}
          aria-pressed={revealed}
          onClick={() => setRevealed((value) => !value)}
        >
          {revealed ? <EyeIcon /> : <EyeOffIcon />}
        </button>
      </div>
    );
  },
);

function EyeIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M10.7 6.2A9.8 9.8 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-3.4 4.3M6.6 6.7A17.6 17.6 0 0 0 2 12s3.5 7 10 7a9.6 9.6 0 0 0 4.1-.9" />
      <path d="m3 3 18 18" />
    </svg>
  );
}
