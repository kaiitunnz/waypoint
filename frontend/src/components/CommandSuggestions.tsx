"use client";

import { forwardRef, Fragment, MutableRefObject } from "react";

import { HighlightedText } from "@/components/HighlightedText";
import {
  rowDescription,
  rowDescriptionPositions,
  rowHint,
  rowKey,
  rowLabel,
  rowSection,
  type SuggestionRow,
} from "@waypoint/web-core/composer-completions";

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
          const isWorkspace = rowSection(entry) === "workspace";
          const startsWorkspace =
            isWorkspace && rowSection(suggestions[index - 1]) === "dir";
          return (
            <Fragment key={rowKey(entry)}>
              {startsWorkspace ? (
                <li role="presentation" className="slash-section">
                  Across workspace
                </li>
              ) : null}
              <li>
                <button
                  ref={(node) => {
                    itemRefs.current[index] = node;
                  }}
                  type="button"
                  role="option"
                  aria-selected={index === activeIndex}
                  className={`slash-suggestion${isWorkspace ? " path-row" : ""}${index === activeIndex ? " active" : ""}`}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    onApply(index);
                  }}
                  onMouseEnter={() => onHover(index)}
                >
                  <span className="slash-name">
                    <HighlightedText
                      text={rowLabel(entry)}
                      positions={entry.positions}
                      breakAfterSlash={isWorkspace}
                    />
                    {hint ? <span className="slash-hint">{hint}</span> : null}
                  </span>
                  {description ? (
                    <span className="slash-desc">
                      <HighlightedText
                        text={description}
                        positions={rowDescriptionPositions(entry)}
                      />
                    </span>
                  ) : null}
                </button>
              </li>
            </Fragment>
          );
        })}
      </ul>
    );
  },
);
