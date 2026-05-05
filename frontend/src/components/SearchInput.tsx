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
  );
}
