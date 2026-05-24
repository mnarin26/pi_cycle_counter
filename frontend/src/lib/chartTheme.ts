export const MOLD_PALETTE = [
  "#38bdf8",
  "#4ade80",
  "#fbbf24",
  "#f472b6",
  "#a78bfa",
  "#fb923c",
  "#2dd4bf",
  "#f87171",
];

export const EVENT_COLORS: Record<string, string> = {
  no_movement: "#ef4444",
  cycle_outlier: "#f59e0b",
  reflector_weak: "#eab308",
  camera_disconnect: "#94a3b8",
  default: "#64748b",
};

export function moldColor(key: string, index: number): string {
  return MOLD_PALETTE[index % MOLD_PALETTE.length];
}

/** Stable mold → color map (sorted names; unassigned "—" last). */
export function buildMoldColorMap(names: Iterable<string>): Map<string, string> {
  const uniq = [...new Set(names)];
  const sorted = uniq.sort((a, b) => {
    if (a === "—") return 1;
    if (b === "—") return -1;
    return a.localeCompare(b, "tr");
  });
  const map = new Map<string, string>();
  sorted.forEach((name, i) => map.set(name, moldColor(name, i)));
  return map;
}

export function moldColorFromMap(map: Map<string, string>, name: string | null | undefined): string {
  const key = name || "—";
  return map.get(key) ?? moldColor(key, map.size);
}

export function eventColor(type: string): string {
  return EVENT_COLORS[type] ?? EVENT_COLORS.default;
}

const DISPLAY_TZ = "Europe/Istanbul";

/** API timestamps are UTC (Z); naive strings are treated as UTC, not local. */
export function parseApiTime(iso: string): number {
  if (!iso) return NaN;
  const normalized =
    iso.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso.replace(" ", "T")}Z`;
  return new Date(normalized).getTime();
}

export function formatAxisTime(ms: number, range: string): string {
  const base: Intl.DateTimeFormatOptions = { timeZone: DISPLAY_TZ };
  if (range === "yearly") {
    return new Date(ms).toLocaleDateString("tr-TR", {
      ...base,
      month: "short",
      year: "2-digit",
    });
  }
  if (range === "monthly") {
    return new Date(ms).toLocaleDateString("tr-TR", {
      ...base,
      day: "numeric",
      month: "short",
    });
  }
  return new Date(ms).toLocaleString("tr-TR", {
    ...base,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function chartScrollWidth(pointCount: number, pxPerPoint = 44): number {
  return Math.min(16000, Math.max(720, pointCount * pxPerPoint));
}

export const MS_PER_HOUR = 3_600_000;
export const MS_PER_DAY = 86_400_000;

export type ZigzagResolution = "1h" | "6h" | "12h" | "24h";

export const ZIGZAG_RESOLUTION_MS: Record<ZigzagResolution, number> = {
  "1h": MS_PER_HOUR,
  "6h": 6 * MS_PER_HOUR,
  "12h": 12 * MS_PER_HOUR,
  "24h": MS_PER_DAY,
};

export const ZIGZAG_RESOLUTION_LABELS: Record<ZigzagResolution, string> = {
  "1h": "1 saat / ekran",
  "6h": "6 saat / ekran",
  "12h": "12 saat / ekran",
  "24h": "24 saat / ekran",
};

/** X-axis tick spacing for zigzag timeline (Istanbul wall clock). */
export const ZIGZAG_X_TICK_INTERVAL_MS: Record<ZigzagResolution, number> = {
  "1h": 10 * 60 * 1000,
  "6h": MS_PER_HOUR,
  "12h": 2 * MS_PER_HOUR,
  "24h": 2 * MS_PER_HOUR,
};

const IST_OFFSET_MS = 3 * MS_PER_HOUR;

export function alignMsToIstanbulStep(ms: number, stepMs: number): number {
  const local = ms + IST_OFFSET_MS;
  return Math.floor(local / stepMs) * stepMs - IST_OFFSET_MS;
}

/** Fixed tick positions on the scrollable zigzag time axis. */
export function zigzagXAxisTicks(
  domain: [number, number] | undefined,
  resolution: ZigzagResolution,
): number[] {
  if (!domain) return [];
  const [start, end] = domain;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return [];
  const step = ZIGZAG_X_TICK_INTERVAL_MS[resolution];
  const ticks: number[] = [];
  let t = alignMsToIstanbulStep(start, step);
  if (t < start) t += step;
  while (t <= end) {
    ticks.push(t);
    t += step;
  }
  return ticks;
}

export function formatZigzagAxisTick(ms: number, resolution: ZigzagResolution): string {
  const base: Intl.DateTimeFormatOptions = { timeZone: DISPLAY_TZ };
  if (resolution === "1h") {
    return new Date(ms).toLocaleString("tr-TR", {
      ...base,
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  if (resolution === "6h") {
    return new Date(ms).toLocaleString("tr-TR", {
      ...base,
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return new Date(ms).toLocaleString("tr-TR", {
    ...base,
    day: "numeric",
    month: "short",
    hour: "2-digit",
  });
}

/** Total scrollable timeline for the selected range (calendar window). */
export function zigzagTimelineMs(range: string, windowFromMs: number, windowToMs: number): number {
  const actualSpan = Math.max(MS_PER_HOUR, windowToMs - windowFromMs);
  if (range === "daily") return Math.max(MS_PER_DAY, actualSpan);
  if (range === "weekly") return Math.max(7 * MS_PER_DAY, actualSpan);
  return actualSpan;
}

/** Chart width: each viewport page = visibleSpanMs of wall-clock time. */
export function zigzagChartWidth(
  timelineMs: number,
  visibleSpanMs: number,
  viewportPx: number,
): number {
  const vw = Math.max(640, viewportPx);
  const pages = Math.max(1, Math.ceil(timelineMs / Math.max(MS_PER_HOUR, visibleSpanMs)));
  return pages * vw;
}

/** Fixed X domain — linear time; gaps stay empty. */
export function zigzagXDomain(
  range: string,
  windowFromMs: number,
  windowToMs: number,
): [number, number] {
  const timeline = zigzagTimelineMs(range, windowFromMs, windowToMs);
  return [windowFromMs, windowFromMs + timeline];
}

export function formatBucketLabel(bucket: string, resolution: string): string {
  if (resolution === "month") {
    const [y, m] = bucket.split("-");
    return `${m}/${String(y).slice(2)}`;
  }
  if (resolution === "day") {
    const d = new Date(`${bucket}T12:00:00Z`);
    return d.toLocaleDateString("tr-TR", { day: "numeric", month: "short" });
  }
  const d = new Date(bucket.replace(" ", "T") + ":00:00Z");
  return d.toLocaleString("tr-TR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
  });
}

export function moldStackKey(name: string): string {
  return `mold__${name.replace(/[^a-zA-Z0-9ğüşıöçĞÜŞİÖÇ]+/g, "_")}`;
}

export type ZigzagPoint = {
  t: string;
  t_ms: number;
  cycle_time_s: number | null;
  mold: string;
  mold_color: string;
  is_gap?: boolean;
  gap_ms?: number;
};

export const ZIGZAG_CHART_MARGIN = { top: 8, right: 16, bottom: 24, left: 8 };

/** Map Recharts cursor X → wall-clock ms (number axis + gap markers in data). */
export function resolveZigzagHoverMs(
  label: string | number | undefined,
  coordinate: { x?: number; y?: number } | undefined,
  xDomain: [number, number] | undefined,
  chartWidth: number,
): number | null {
  if (typeof label === "number" && Number.isFinite(label) && label > 1e11) {
    return label;
  }
  if (typeof label === "string") {
    const n = Number(label);
    if (Number.isFinite(n) && n > 1e11) return n;
  }
  if (coordinate?.x != null && xDomain) {
    const innerW = Math.max(
      1,
      chartWidth - ZIGZAG_CHART_MARGIN.left - ZIGZAG_CHART_MARGIN.right,
    );
    const ratio = Math.max(
      0,
      Math.min(1, (coordinate.x - ZIGZAG_CHART_MARGIN.left) / innerW),
    );
    return xDomain[0] + ratio * (xDomain[1] - xDomain[0]);
  }
  return null;
}

export function findNearestZigzagCycle(
  plotPoints: ZigzagPoint[],
  hoverMs: number,
): { point: ZigzagPoint; deltaMs: number } | null {
  if (!plotPoints.length || !Number.isFinite(hoverMs)) return null;

  let lo = 0;
  let hi = plotPoints.length - 1;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (plotPoints[mid].t_ms < hoverMs) lo = mid + 1;
    else hi = mid;
  }

  const candidates: ZigzagPoint[] = [];
  if (lo > 0) candidates.push(plotPoints[lo - 1]);
  candidates.push(plotPoints[lo]);

  let best = candidates[0];
  let bestD = Math.abs(best.t_ms - hoverMs);
  for (let i = 1; i < candidates.length; i++) {
    const d = Math.abs(candidates[i].t_ms - hoverMs);
    if (d < bestD) {
      bestD = d;
      best = candidates[i];
    }
  }
  return { point: best, deltaMs: bestD };
}

export type ZigzagLineSegment = {
  mold: string;
  color: string;
  points: ZigzagPoint[];
};

/** Split zigzag into colored line segments (mold change or duruş boşluğu). */
export function buildZigzagLineSegments(data: ZigzagPoint[]): ZigzagLineSegment[] {
  const segments: ZigzagLineSegment[] = [];
  let current: ZigzagPoint[] = [];

  const flush = () => {
    if (current.length >= 2) {
      const last = current[current.length - 1];
      segments.push({
        mold: last.mold,
        color: last.mold_color,
        points: [...current],
      });
    }
    current = [];
  };

  for (const p of data) {
    if (p.is_gap || p.cycle_time_s == null) {
      flush();
      continue;
    }
    if (current.length && current[current.length - 1].mold !== p.mold) {
      const bridge = current[current.length - 1];
      if (current.length >= 2) {
        segments.push({
          mold: bridge.mold,
          color: bridge.mold_color,
          points: [...current],
        });
      }
      current = [bridge, p];
    } else {
      current.push(p);
    }
  }
  flush();
  return segments;
}

export function buildZigzagSeries(
  series: Array<{ t: string; cycle_time_s: number; mold: string | null }>,
  gapThresholdMs: number,
  colorMap: Map<string, string>,
): ZigzagPoint[] {
  const out: ZigzagPoint[] = [];
  let prevMs = 0;
  for (const x of series) {
    const t_ms = parseApiTime(x.t);
    const mold = x.mold || "—";
    const color = moldColorFromMap(colorMap, mold);

    if (prevMs > 0 && t_ms - prevMs >= gapThresholdMs) {
      out.push({
        t: new Date(prevMs + (t_ms - prevMs) / 2).toISOString(),
        t_ms: prevMs + (t_ms - prevMs) / 2,
        cycle_time_s: null,
        mold: "",
        mold_color: "transparent",
        is_gap: true,
        gap_ms: t_ms - prevMs,
      });
    }
    out.push({
      t: x.t,
      t_ms,
      cycle_time_s: Number(x.cycle_time_s.toFixed(3)),
      mold,
      mold_color: color,
    });
    prevMs = t_ms;
  }
  return out;
}
