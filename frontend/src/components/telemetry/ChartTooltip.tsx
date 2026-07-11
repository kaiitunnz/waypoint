"use client";

export interface ChartTooltipRow {
  key: string;
  label: string;
  value: string;
  color?: string;
}

export interface ChartTooltipState {
  left: number;
  top: number;
  title: string;
  rows: ChartTooltipRow[];
}

// Shared hover/focus readout for the hand-built SVG charts. Values lead,
// labels follow (dataviz skill's interaction convention); every value here is
// also reachable without hovering via the chart's text summary / table view.
export function ChartTooltip({ state }: { state: ChartTooltipState | null }) {
  if (!state) return null;
  return (
    <div
      className="tm-chart-tooltip"
      style={{ left: state.left, top: state.top }}
      role="status"
    >
      <p className="tm-chart-tooltip-title">{state.title}</p>
      <ul className="tm-chart-tooltip-rows">
        {state.rows.map((row) => (
          <li key={row.key} className="tm-chart-tooltip-row">
            {row.color ? (
              <span
                className="tm-chart-tooltip-key"
                aria-hidden="true"
                style={{ background: row.color }}
              />
            ) : null}
            <strong>{row.value}</strong>
            <span className="muted">{row.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
