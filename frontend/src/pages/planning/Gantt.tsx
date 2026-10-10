import type { GanttBar, GanttRow } from "./types";
import { fmtDate } from "./types";

function pos(start: number, end: number, a: string, b: string) {
  const s = Math.max(start, new Date(a).getTime());
  const e = Math.min(end, new Date(b).getTime());
  const span = end - start || 1;
  return { left: `${((s - start) / span) * 100}%`, width: `${Math.max(0.4, ((e - s) / span) * 100)}%`, ok: e > s };
}

function Bar({ bar, start, end, cls }: { bar: GanttBar; start: number; end: number; cls: string }) {
  const p = pos(start, end, bar.start, bar.end);
  if (!p.ok) return null;
  const tip = `${bar.label}\n${fmtDate(bar.start)} → ${fmtDate(bar.end)}`;
  const co =
    bar.prod_start && new Date(bar.prod_start).getTime() > new Date(bar.start).getTime()
      ? pos(start, end, bar.start, bar.prod_start)
      : null;
  return (
    <>
      {co?.ok && (
        <div
          className="absolute top-0.5 h-5 rounded-l bg-[repeating-linear-gradient(45deg,#475569_0,#475569_3px,#334155_3px,#334155_6px)]"
          style={{ left: co.left, width: co.width }}
          title={`Kalıp değişimi\n${tip}`}
        />
      )}
      <div
        className={`absolute top-0.5 h-5 overflow-hidden whitespace-nowrap rounded px-1 text-[10px] leading-5 ${cls}`}
        style={{ left: p.left, width: p.width }}
        title={tip}
      >
        {bar.label}
      </div>
    </>
  );
}

export function Gantt({ start, end, rows }: { start: string; end: string; rows: GanttRow[] }) {
  const s = new Date(start).getTime();
  const e = new Date(end).getTime();
  const days: number[] = [];
  const d0 = new Date(start);
  d0.setHours(0, 0, 0, 0);
  for (let t = d0.getTime() + 86400000; t < e; t += 86400000) days.push(t);
  const step = Math.max(1, Math.ceil(days.length / 14));
  return (
    <div className="overflow-x-auto rounded border border-slate-700 bg-slate-900/60 p-2">
      <div className="min-w-[720px]">
        <div className="relative ml-28 h-5 text-[10px] text-slate-400">
          {days.map((t, i) =>
            i % step === 0 ? (
              <span key={t} className="absolute -translate-x-1/2" style={{ left: `${((t - s) / (e - s)) * 100}%` }}>
                {new Date(t).toLocaleDateString("tr-TR", { day: "numeric", month: "short" })}
              </span>
            ) : null,
          )}
        </div>
        {rows.map((r) => (
          <div key={r.machine_id} className="border-t border-slate-800 py-1">
            {(["planned", "actual"] as const).map((lane) => (
              <div key={lane} className="flex items-center">
                <div className="w-28 shrink-0 truncate pr-2 text-xs text-slate-300">
                  {lane === "planned" ? r.machine_name : ""}
                  <span className="ml-1 text-[10px] text-slate-500">{lane === "planned" ? "Plan" : "Gerçek"}</span>
                </div>
                <div className="relative h-6 flex-1 rounded bg-slate-800/60">
                  {days.map((t) => (
                    <div
                      key={t}
                      className="absolute top-0 h-6 border-l border-slate-700/60"
                      style={{ left: `${((t - s) / (e - s)) * 100}%` }}
                    />
                  ))}
                  {lane === "planned"
                    ? r.planned.map((b, i) => (
                        <Bar key={i} bar={b} start={s} end={e} cls={b.refill ? "bg-amber-600 text-white" : "bg-sky-600 text-white"} />
                      ))
                    : [
                        ...r.downtimes.map((b, i) => (
                          <Bar key={`d${i}`} bar={{ ...b, label: `Duruş: ${b.label}` }} start={s} end={e} cls="bg-red-700/80 text-white" />
                        )),
                        ...r.actual.map((b, i) => (
                          <Bar
                            key={`a${i}`}
                            bar={b}
                            start={s}
                            end={e}
                            cls={b.relevant ? "bg-emerald-600 text-white" : "bg-slate-600 text-slate-200"}
                          />
                        )),
                      ]}
                </div>
              </div>
            ))}
          </div>
        ))}
        <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-slate-400">
          <span><span className="mr-1 inline-block h-2 w-3 rounded bg-sky-600" />Planlanan</span>
          <span><span className="mr-1 inline-block h-2 w-3 rounded bg-amber-600" />Asgari stok tamamlama</span>
          <span><span className="mr-1 inline-block h-2 w-3 rounded bg-emerald-600" />Gerçekleşen (bu siparişler)</span>
          <span><span className="mr-1 inline-block h-2 w-3 rounded bg-slate-600" />Gerçekleşen (diğer işler)</span>
          <span><span className="mr-1 inline-block h-2 w-3 rounded bg-red-700" />Duruş</span>
          <span><span className="mr-1 inline-block h-2 w-3 rounded bg-slate-500" />Kalıp değişimi (taralı)</span>
        </div>
      </div>
    </div>
  );
}
