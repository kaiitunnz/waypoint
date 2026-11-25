import { Fragment } from "react";

import type { HighlightToken } from "@waypoint/web-core/highlight";

export function Tokens({ tokens }: { tokens: HighlightToken[] }) {
  return tokens.map((token, index) =>
    token.className ? (
      <span key={index} className={token.className}>
        {token.text}
      </span>
    ) : (
      <Fragment key={index}>{token.text}</Fragment>
    ),
  );
}
