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

export function eventColor(type: string): string {
  return EVENT_COLORS[type] ?? EVENT_COLORS.default;
}

export function formatAxisTime(ms: number, range: string): string {
  const d = new Date(ms);
  if (range === "yearly") {
    return d.toLocaleDateString("tr-TR", { month: "short", year: "2-digit" });
  }
  if (range === "monthly") {
    return d.toLocaleDateString("tr-TR", { day: "numeric", month: "short" });
  }
  return d.toLocaleString("tr-TR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function chartScrollWidth(pointCount: number): number {
  return Math.min(16000, Math.max(720, pointCount * 3));
}
