"use client";

import { UsageTone } from "@/lib/usage";

interface UsageDialProps {
  percent: number | null;
  tone: UsageTone;
  label: string;
  caption?: string | null;
  size?: "lg" | "md";
}

const ARC_START = 135;
const ARC_END = 405;
const ARC_SWEEP = ARC_END - ARC_START;
const RADIUS = 56;
const CENTER = 72;
const VIEWBOX = 144;
const STROKE_WIDTH = 8;
const TICK_COUNT = 13;

function polar(angleDeg: number, r: number): [number, number] {
  const rad = (Math.PI / 180) * angleDeg;
  return [CENTER + r * Math.cos(rad), CENTER + r * Math.sin(rad)];
}

function arcPath(startDeg: number, endDeg: number, r: number): string {
  const [sx, sy] = polar(startDeg, r);
  const [ex, ey] = polar(endDeg, r);
  const largeArc = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${sx} ${sy} A ${r} ${r} 0 ${largeArc} 1 ${ex} ${ey}`;
}

export function UsageDial({
  percent,
  tone,
  label,
  caption,
  size = "lg",
}: UsageDialProps) {
  const safePercent = percent === null ? 0 : Math.max(0, Math.min(100, percent));
  const fillEndDeg = ARC_START + (ARC_SWEEP * safePercent) / 100;
  const needleDeg = fillEndDeg;
  const trackPath = arcPath(ARC_START, ARC_END, RADIUS);
  const fillPath = percent === null ? "" : arcPath(ARC_START, fillEndDeg, RADIUS);

  const ticks = Array.from({ length: TICK_COUNT }, (_, i) => {
    const angle = ARC_START + (ARC_SWEEP * i) / (TICK_COUNT - 1);
    const inner = i % 3 === 0 ? RADIUS - 14 : RADIUS - 9;
    const [x1, y1] = polar(angle, RADIUS - 4);
    const [x2, y2] = polar(angle, inner);
    return { x1, y1, x2, y2, major: i % 3 === 0, angle };
  });

  const [needleTipX, needleTipY] = polar(needleDeg, RADIUS - 6);
  const [needleBaseX, needleBaseY] = polar(needleDeg + 180, 10);

  return (
    <div className={`usage-dial size-${size} tone-${tone}`}>
      <svg
        viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`}
        className="usage-dial-svg"
        role="img"
        aria-label={
          percent === null
            ? `${label}: no data`
            : `${label}: ${Math.round(safePercent)}%`
        }
      >
        <circle
          cx={CENTER}
          cy={CENTER}
          r={RADIUS + 6}
          className="usage-dial-bezel"
        />
        <circle
          cx={CENTER}
          cy={CENTER}
          r={RADIUS + 2}
          className="usage-dial-face"
        />

        {ticks.map((tick, i) => (
          <line
            key={i}
            x1={tick.x1}
            y1={tick.y1}
            x2={tick.x2}
            y2={tick.y2}
            className={`usage-dial-tick${tick.major ? " is-major" : ""}`}
          />
        ))}

        <path
          d={trackPath}
          className="usage-dial-track"
          fill="none"
          strokeWidth={STROKE_WIDTH}
          strokeLinecap="round"
        />

        {percent !== null ? (
          <path
            d={fillPath}
            className="usage-dial-fill"
            fill="none"
            strokeWidth={STROKE_WIDTH}
            strokeLinecap="round"
          />
        ) : null}

        {percent !== null ? (
          <>
            <line
              x1={needleBaseX}
              y1={needleBaseY}
              x2={needleTipX}
              y2={needleTipY}
              className="usage-dial-needle"
              strokeLinecap="round"
            />
            <circle
              cx={CENTER}
              cy={CENTER}
              r={5}
              className="usage-dial-hub"
            />
            <circle
              cx={CENTER}
              cy={CENTER}
              r={2}
              className="usage-dial-hub-cap"
            />
          </>
        ) : (
          <text
            x={CENTER}
            y={CENTER + 4}
            className="usage-dial-empty"
            textAnchor="middle"
          >
            —
          </text>
        )}
      </svg>

      <div className="usage-dial-readout">
        <span className="usage-dial-percent">
          <strong>{percent === null ? "—" : Math.round(safePercent)}</strong>
          <em>%</em>
        </span>
        <span className="usage-dial-label">{label}</span>
        {caption ? <span className="usage-dial-caption">{caption}</span> : null}
      </div>
    </div>
  );
}
