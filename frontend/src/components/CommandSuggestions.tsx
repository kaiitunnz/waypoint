"use client";

import { forwardRef, MutableRefObject } from "react";

import {
  rowDescription,
  rowHint,
  rowKey,
  rowLabel,
  type SuggestionRow,
} from "@/lib/composer-completions";

interface CommandSuggestionsProps {
  suggestions: ReadonlyArray<SuggestionRow>;
  activeIndex: number;
  itemRefs: MutableRefObject<Array<HTMLButtonElement | null>>;
  onApply: (index: number) => void;
  onHover: (index: number) => void;
}

export const CommandSuggestions = forwardRef<HTMLUListElement, CommandSuggestionsProps>(
  function CommandSuggestions(
    { suggestions, activeIndex, itemRefs, onApply, onHover },
    ref,
  ) {
    return (
      <ul className="slash-suggestions" role="listbox" ref={ref}>
        {suggestions.map((entry, index) => {
          const hint = rowHint(entry);
          const description = rowDescription(entry);
          return (
            <li key={rowKey(entry)}>
              <button
                ref={(node) => {
                  itemRefs.current[index] = node;
                }}
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                className={`slash-suggestion ${index === activeIndex ? "active" : ""}`}
                onMouseDown={(event) => {
                  event.preventDefault();
                  onApply(index);
                }}
                onMouseEnter={() => onHover(index)}
              >
                <span className="slash-name">
                  {rowLabel(entry)}
                  {hint ? <span className="slash-hint">{hint}</span> : null}
                </span>
                {description ? (
                  <span className="slash-desc">{description}</span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    );
  },
);
