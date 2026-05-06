"use client";

import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  showStatusExample?: boolean;
  autoFocus?: boolean;
}

export function SearchInput({
  value,
  onChange,
  placeholder,
  className = "",
  showStatusExample = true,
  autoFocus = false,
}: SearchInputProps) {
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const [tooltipPos, setTooltipPos] = useState<{ top: number; right: number } | null>(null);
  const iconRef = useRef<HTMLDivElement | null>(null);
  const closeTimerRef = useRef<number | null>(null);

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onChange("");
    }
  }

  function updatePosition() {
    const el = iconRef.current;
    if (!el) {
      return;
    }
    const rect = el.getBoundingClientRect();
    setTooltipPos({
      top: rect.bottom + 8,
      right: window.innerWidth - rect.right,
    });
  }

  function cancelClose() {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }

  function openTooltip() {
    cancelClose();
    updatePosition();
    setTooltipOpen(true);
  }

  function scheduleClose() {
    cancelClose();
    closeTimerRef.current = window.setTimeout(() => {
      setTooltipOpen(false);
      closeTimerRef.current = null;
    }, 120);
  }

  useEffect(() => {
    if (!tooltipOpen) {
      return;
    }
    function reposition() {
      updatePosition();
    }
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [tooltipOpen]);

  useEffect(() => {
    return () => {
      cancelClose();
    };
  }, []);

  const tooltipNode =
    tooltipOpen && tooltipPos && typeof document !== "undefined"
      ? createPortal(
          <div
            className="help-tooltip"
            role="tooltip"
            style={{ position: "fixed", top: tooltipPos.top, right: tooltipPos.right }}
            onMouseEnter={openTooltip}
            onMouseLeave={scheduleClose}
          >
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
              {showStatusExample ? (
                <li>
                  <code>status:active</code>
                  <span>Filter by session state</span>
                </li>
              ) : null}
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
          </div>,
          document.body,
        )
      : null;

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
          autoFocus={autoFocus}
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
      <div
        className="help-icon"
        ref={iconRef}
        aria-label="Search syntax help"
        tabIndex={0}
        onMouseEnter={openTooltip}
        onMouseLeave={scheduleClose}
        onFocus={openTooltip}
        onBlur={scheduleClose}
      >
        ?
      </div>
      {tooltipNode}
    </div>
  );
}
