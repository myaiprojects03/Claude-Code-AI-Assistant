import { useMemo, useRef, useState } from "react";

/** Single-series column chart (no legend: the panel title names the series).
 *  Columns <=24px, 4px rounded tops, square at the baseline, 2px surface gap, hairline grid,
 *  per-bar hover + keyboard-focus tooltip, and a table view for screen readers. */
export interface Point {
  label: string; // axis label
  full: string; // tooltip label
  value: number;
}

const W = 640;
const H = 200;
const PAD = { top: 18, right: 4, bottom: 24, left: 30 };

function niceMax(v: number) {
  if (v <= 4) return 4;
  const pow = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].find((s) => (v / (s * pow)) <= 4) ?? 10;
  return Math.ceil(v / (step * pow)) * step * pow;
}

export function BarChart({ data, unit, caption }: { data: Point[]; unit: string; caption: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const max = useMemo(() => niceMax(Math.max(0, ...data.map((d) => d.value))), [data]);
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const band = innerW / data.length;
  const barW = Math.min(24, band - 2);
  const y = (v: number) => PAD.top + innerH - (v / max) * innerH;
  const ticks = [0, max / 2, max];
  const peak = data.reduce((best, d, i) => (d.value > data[best].value ? i : best), 0);
  const last = data.length - 1;

  const barPath = (x: number, top: number, w: number, base: number) => {
    const h = base - top;
    if (h <= 0) return "";
    const r = Math.min(4, h, w / 2);
    return `M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + w - r} Q${x + w},${top} ${x + w},${top + r} V${base} Z`;
  };

  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={caption}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid-line" x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="axis-text" x={PAD.left - 8} y={y(t) + 4} textAnchor="end">
              {Math.round(t)}
            </text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = PAD.left + i * band + (band - barW) / 2;
          const base = PAD.top + innerH;
          const top = y(d.value);
          const showLabel = (i === peak || i === last) && d.value > 0;
          return (
            <g key={d.full}>
              <rect
                className="hit"
                x={PAD.left + i * band}
                y={PAD.top}
                width={band}
                height={innerH}
                tabIndex={0}
                aria-label={`${d.full}: ${d.value} ${unit}`}
                onPointerEnter={() => setHover(i)}
                onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
              />
              <path className={`bar${i === last ? " current" : ""}`} d={barPath(x, top, barW, base)} pointerEvents="none" />
              {d.value === 0 && <line x1={x} x2={x + barW} y1={base - 0.5} y2={base - 0.5} stroke="var(--bar)" />}
              {showLabel && (
                <text className="bar-label num" x={x + barW / 2} y={top - 6} textAnchor="middle" pointerEvents="none">
                  {d.value}
                </text>
              )}
              {(i % 2 === (last % 2) || data.length <= 7) && (
                <text className="axis-text" x={x + barW / 2} y={H - 6} textAnchor="middle" pointerEvents="none">
                  {d.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div
          className="tooltip"
          role="status"
          style={{
            left: `${((PAD.left + hover * band + band / 2) / W) * 100}%`,
            top: `${(y(data[hover].value) / H) * 100}%`,
          }}
        >
          <span className="v num">
            {data[hover].value} {unit}
          </span>
          {data[hover].full}
        </div>
      )}
      <table className="sr-only">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th>Day</th>
            <th>{unit}</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.full}>
              <td>{d.full}</td>
              <td>{d.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
