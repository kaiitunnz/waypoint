"use client";

import { forwardRef, Fragment, MutableRefObject } from "react";

import { HighlightedText } from "@/components/HighlightedText";
import {
  rowDescription,
  rowDescriptionPositions,
  rowHint,
  rowKey,
  rowLabel,
  type SuggestionRow,
} from "@/lib/composer-completions";

function rowSection(row: SuggestionRow | undefined): string | null {
  return row?.type === "workspace_path" ? row.section : null;
}

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
          // Caption the repo-wide group only where it follows directory rows.
          const startsWorkspace =
            rowSection(entry) === "workspace" &&
            rowSection(suggestions[index - 1]) === "dir";
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
                  className={`slash-suggestion${rowSection(entry) === "workspace" ? " path-row" : ""}${index === activeIndex ? " active" : ""}`}
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
                      breakAfterSlash={rowSection(entry) === "workspace"}
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
