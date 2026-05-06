import { KeyboardEvent } from "react";

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

export function SearchInput({ value, onChange, placeholder, className = "" }: SearchInputProps) {
  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onChange("");
    }
  }

  return (
    <div className={`search-bar ${className}`}>
      <div className="input-wrapper">
        <input
          type="text" // using text to avoid browser-native clear buttons
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          aria-label={placeholder || "Search"}
        />
        {value !== "" ? (
          <button
            type="button"
            className="link-button clear-btn"
            onClick={() => onChange("")}
            title="Clear search"
            aria-label="Clear search"
          >
            ×
          </button>
        ) : null}
      </div>
      <div className="help-icon" aria-label="Search syntax help" tabIndex={0}>
        ?
        <div className="help-tooltip" role="tooltip">
          <p>Advanced Search Syntax:</p>
          <ul>
            <li>
              <code>field:value</code>
              <span>Specific field (title, branch, etc.)</span>
            </li>
            <li>
              <code>agent:opencode</code>
              <span>Filter by backend</span>
            </li>
            <li>
              <code>status:active</code>
              <span>Filter by session state</span>
            </li>
            <li>
              <code>&quot;exact phrase&quot;</code>
              <span>Match exact phrase</span>
            </li>
            <li>
              <code>/regex/i</code>
              <span>Regular expression match</span>
            </li>
            <li>
              <code>AND / OR</code>
              <span>Boolean operators (spaces act as AND)</span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}
