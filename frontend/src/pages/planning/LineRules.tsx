import { useMemo, useState } from "react";
import type { LineRule, RuleLine, WeekendMode } from "./types";
import { fmtDate, fmtQty } from "./types";

const EMPTY: LineRule = { hours_per_day: null, saturday: null, sunday: null };

const WEEKEND_OPTS: Array<[WeekendMode | "", string]> = [
  ["", "Varsayılan"],
  ["work", "Çalışılır"],
  ["avoid", "Mümkünse çalışma"],
  ["off", "Çalışılmaz"],
];

function clean(rules: Record<string, LineRule>): Record<string, LineRule> {
  const out: Record<string, LineRule> = {};
  for (const [k, r] of Object.entries(rules)) {
    if (r.hours_per_day !== null || r.saturday || r.sunday) out[k] = r;
  }
  return out;
}

function parseHours(v: string): number | null {
  if (!v.trim()) return null;
  const n = Number(v.replace(",", "."));
  return Number.isFinite(n) && n > 0 ? n : null;
}

const clampHours = (n: number | null) => (n === null ? null : Math.min(24, Math.max(0.5, n)));

export function LineRules({
  lines,
  value,
  onChange,
  shiftText,
}: {
  lines: RuleLine[];
  value: Record<string, LineRule>;
  onChange: (v: Record<string, LineRule>) => void;
  shiftText?: string;
}) {
  const [filter, setFilter] = useState("");
  const [bulk, setBulk] = useState("");
  const shown = useMemo(() => {
    const q = filter.trim().toLocaleLowerCase("tr");
    return q ? lines.filter((l) => l.label.toLocaleLowerCase("tr").includes(q)) : lines;
  }, [lines, filter]);
  const count = Object.keys(value).length;

  const setRule = (key: string, patch: Partial<LineRule>) =>
    onChange(clean({ ...value, [key]: { ...EMPTY, ...value[key], ...patch } }));

  const applyBulk = (hours: number | null) => {
    const next = { ...value };
    for (const l of shown) next[l.key] = { ...EMPTY, ...next[l.key], hours_per_day: hours };
    onChange(clean(next));
  };

  return (
    <details className="text-sm md:col-span-2">
      <summary className="cursor-pointer text-slate-200">
        Siparişe özel çalışma saati {count > 0 ? `(${count} siparişte özel kural)` : ""}
      </summary>
      <div className="mt-2 space-y-2">
        <p className="text-xs text-slate-400">
          «Günde X saat» o siparişin üretimini günde X saatle sınırlar (önce gündüz vardiyası doldurulur). Boş bırakılan
          siparişler Ayarlar'daki vardiyalar boyunca (tam vardiya) çalışır{shiftText ? ` (${shiftText})` : ""}. Hafta sonu
          «Varsayılan» ise yukarıdaki Cumartesi/Pazar seçimi geçerlidir.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="w-56 rounded bg-slate-800 px-2 py-1 text-xs"
            placeholder="Ürün ara…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <span className="text-xs text-slate-400">Görünen {shown.length} siparişe:</span>
          <input
            className="w-16 rounded bg-slate-800 px-2 py-1 text-xs"
            inputMode="decimal"
            placeholder="saat"
            value={bulk}
            onChange={(e) => setBulk(e.target.value)}
          />
          <button
            type="button"
            className="rounded bg-slate-700 px-2 py-1 text-xs disabled:opacity-40"
            disabled={parseHours(bulk) === null || shown.length === 0}
            onClick={() => applyBulk(clampHours(parseHours(bulk)))}
          >
            günde bu kadar saat
          </button>
          <button
            type="button"
            className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-300"
            disabled={shown.length === 0}
            onClick={() => applyBulk(null)}
          >
            saat sınırını kaldır
          </button>
        </div>
        {lines.length === 0 ? (
          <p className="text-xs text-slate-500">Açık sipariş yok.</p>
        ) : (
          <div className="max-h-80 overflow-auto rounded border border-slate-700">
            <table className="min-w-full text-left text-xs">
              <thead className="sticky top-0 bg-slate-800 text-slate-400">
                <tr>
                  <th className="px-2 py-1">Termin</th>
                  <th className="px-2 py-1">Ürün</th>
                  <th className="px-2 py-1">Miktar</th>
                  <th className="px-2 py-1">Günde saat</th>
                  <th className="px-2 py-1">Cumartesi</th>
                  <th className="px-2 py-1">Pazar</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((l) => {
                  const r = value[l.key] ?? EMPTY;
                  return (
                    <tr key={l.key} className={`border-t border-slate-800 ${value[l.key] ? "bg-sky-950/40" : ""}`}>
                      <td className="px-2 py-1 whitespace-nowrap">{fmtDate(l.due, false)}</td>
                      <td className="px-2 py-1">{l.label}</td>
                      <td className="px-2 py-1">{fmtQty(l.qty)}</td>
                      <td className="px-2 py-1">
                        <input
                          type="number"
                          min={0.5}
                          max={24}
                          step={0.5}
                          className="w-20 rounded bg-slate-800 px-1 py-0.5"
                          placeholder="vardiya"
                          value={r.hours_per_day ?? ""}
                          onChange={(e) => setRule(l.key, { hours_per_day: parseHours(e.target.value) })}
                          onBlur={() => r.hours_per_day !== null && setRule(l.key, { hours_per_day: clampHours(r.hours_per_day) })}
                        />
                      </td>
                      {(["saturday", "sunday"] as const).map((day) => (
                        <td key={day} className="px-2 py-1">
                          <select
                            className="rounded bg-slate-800 px-1 py-0.5"
                            value={r[day] ?? ""}
                            onChange={(e) => setRule(l.key, { [day]: (e.target.value || null) as WeekendMode | null })}
                          >
                            {WEEKEND_OPTS.map(([v, label]) => (
                              <option key={v} value={v}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </details>
  );
}
