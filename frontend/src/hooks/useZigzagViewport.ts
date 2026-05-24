import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiGet } from "../api/client";
import { MS_PER_DAY, parseApiTime, zigzagChartWidth, zigzagTimelineMs } from "../lib/chartTheme";

export type ViewportCycle = {
  t: string;
  cycle_time_s: number;
  mold_id?: number | null;
  mold: string | null;
};

type ViewportResponse = {
  series: ViewportCycle[];
  cycle_count: number;
  shown: number;
  truncated: boolean;
};

const VIEWPORT_MAX_POINTS = 4000;
const MIN_SPLIT_MS = 60_000;
const SCROLL_PAD_RATIO = 0.15;

function rangeCovered(ranges: Array<[number, number]>, fromMs: number, toMs: number): boolean {
  return ranges.some(([a, b]) => a <= fromMs && b >= toMs);
}

function mergeRanges(ranges: Array<[number, number]>, fromMs: number, toMs: number): Array<[number, number]> {
  const next = [...ranges, [fromMs, toMs]].sort((a, b) => a[0] - b[0]);
  const out: Array<[number, number]> = [];
  for (const [a, b] of next) {
    if (!out.length || a > out[out.length - 1][1]) {
      out.push([a, b]);
    } else {
      out[out.length - 1][1] = Math.max(out[out.length - 1][1], b);
    }
  }
  return out;
}

export function useZigzagViewport(opts: {
  machineId: number;
  range: string;
  windowFrom: string | undefined;
  windowTo: string | undefined;
  visibleSpanMs: number;
  enabled: boolean;
}) {
  const { machineId, range, windowFrom, windowTo, visibleSpanMs, enabled } = opts;
  const scrollRef = useRef<HTMLDivElement>(null);
  const loadedRangesRef = useRef<Array<[number, number]>>([]);
  const loadGenRef = useRef(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [viewportPx, setViewportPx] = useState(960);
  const [cyclesByKey, setCyclesByKey] = useState<Map<string, ViewportCycle>>(new Map());
  const [loadingViewport, setLoadingViewport] = useState(false);

  const windowFromMs = windowFrom ? parseApiTime(windowFrom) : 0;
  const windowToMs = windowTo ? parseApiTime(windowTo) : 0;
  const timelineMs = useMemo(
    () => (windowFromMs > 0 ? zigzagTimelineMs(range, windowFromMs, windowToMs) : MS_PER_DAY),
    [range, windowFromMs, windowToMs],
  );
  const chartWidth = zigzagChartWidth(timelineMs, visibleSpanMs, viewportPx);

  const mergedSeries = useMemo(() => {
    return Array.from(cyclesByKey.values()).sort(
      (a, b) => parseApiTime(a.t) - parseApiTime(b.t),
    );
  }, [cyclesByKey]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => setViewportPx(Math.max(640, el.clientWidth || 960));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [enabled]);

  const loadRange = useCallback(
    async (fromMs: number, toMs: number, opts?: { bypassCover?: boolean }) => {
      if (!enabled || !machineId || !windowFrom || !windowTo) return;
      const timelineEnd = windowFromMs + timelineMs;
      const clampedFrom = Math.max(windowFromMs, fromMs);
      const clampedTo = Math.min(timelineEnd, windowToMs, toMs);
      if (clampedTo <= clampedFrom) return;
      if (!opts?.bypassCover && rangeCovered(loadedRangesRef.current, clampedFrom, clampedTo)) {
        return;
      }

      const gen = ++loadGenRef.current;
      setLoadingViewport(true);
      try {
        const p = new URLSearchParams({
          machine_id: String(machineId),
          from: new Date(clampedFrom).toISOString(),
          to: new Date(clampedTo).toISOString(),
          max_points: String(VIEWPORT_MAX_POINTS),
        });
        const data = await apiGet<ViewportResponse>(`/api/analytics/cycles_viewport?${p}`);
        if (gen !== loadGenRef.current) return;

        setCyclesByKey((prev) => {
          const next = new Map(prev);
          for (const pt of data.series) next.set(pt.t, pt);
          return next;
        });

        if (data.truncated && clampedTo - clampedFrom > MIN_SPLIT_MS) {
          const mid = Math.floor((clampedFrom + clampedTo) / 2);
          await loadRange(clampedFrom, mid, { bypassCover: true });
          if (gen !== loadGenRef.current) return;
          await loadRange(mid, clampedTo, { bypassCover: true });
          return;
        }

        if (!data.truncated) {
          loadedRangesRef.current = mergeRanges(loadedRangesRef.current, clampedFrom, clampedTo);
        }
      } finally {
        if (gen === loadGenRef.current) setLoadingViewport(false);
      }
    },
    [enabled, machineId, windowFrom, windowTo, windowFromMs, windowToMs, timelineMs],
  );

  const syncViewportFromScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el || !enabled || timelineMs <= 0) return;

    const maxScroll = Math.max(0, el.scrollWidth - el.clientWidth);
    const visibleSpanMs =
      maxScroll > 0 ? (el.clientWidth / el.scrollWidth) * timelineMs : timelineMs;

    const ratio = maxScroll > 0 ? el.scrollLeft / maxScroll : 0;
    const visibleStart = windowFromMs + ratio * Math.max(0, timelineMs - visibleSpanMs);
    const visibleEnd = visibleStart + visibleSpanMs;
    const pad = Math.max(60_000, visibleSpanMs * SCROLL_PAD_RATIO);

    void loadRange(visibleStart - pad, visibleEnd + pad);

    if (ratio >= 0.98) {
      void loadRange(windowFromMs, windowToMs, { bypassCover: true });
    }
  }, [enabled, timelineMs, windowFromMs, windowToMs, loadRange]);

  const scheduleScrollLoad = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(syncViewportFromScroll, 120);
  }, [syncViewportFromScroll]);

  useEffect(() => {
    loadedRangesRef.current = [];
    setCyclesByKey(new Map());
    if (!enabled) return;

    const el = scrollRef.current;
    if (el) el.scrollLeft = 0;

    // Only load the visible scroll window (+ pad), not the entire timeline.
    const id = requestAnimationFrame(() => syncViewportFromScroll());
    return () => cancelAnimationFrame(id);
  }, [
    enabled,
    machineId,
    range,
    windowFrom,
    windowTo,
    windowFromMs,
    windowToMs,
    timelineMs,
    visibleSpanMs,
    syncViewportFromScroll,
  ]);

  useEffect(() => {
    if (!enabled) return;
    const id = requestAnimationFrame(() => syncViewportFromScroll());
    return () => cancelAnimationFrame(id);
  }, [enabled, chartWidth, visibleSpanMs, syncViewportFromScroll]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !enabled) return;
    const onScroll = () => scheduleScrollLoad();
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [enabled, scheduleScrollLoad]);

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  return {
    scrollRef,
    chartWidth,
    viewportPx,
    timelineMs,
    mergedSeries,
    loadingViewport,
    windowFromMs,
    windowToMs,
    syncViewportFromScroll,
  };
}
