"use client";

import { forwardRef, MutableRefObject } from "react";

import type { CommandCompletion } from "@/lib/types";

export function commandLabel(entry: CommandCompletion): string {
  return `${entry.trigger}${entry.name}`;
}

interface CommandSuggestionsProps {
  suggestions: ReadonlyArray<CommandCompletion>;
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
        {suggestions.map((entry, index) => (
          <li key={entry.id}>
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
                {commandLabel(entry)}
                {entry.argument_hint ? (
                  <span className="slash-hint">{entry.argument_hint}</span>
                ) : null}
              </span>
              <span className="slash-desc">{entry.description}</span>
            </button>
          </li>
        ))}
      </ul>
    );
  },
);
