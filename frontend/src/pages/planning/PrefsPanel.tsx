import { useState } from "react";
import type { PlanPrefs, PrefsDefaults } from "./types";

type Opt<T extends string> = Array<[T, string]>;

function Choice<T extends string>({
  label,
  hint,
  value,
  options,
  onChange,
}: {
  label: string;
  hint?: string;
  value: T;
  options: Opt<T>;
  onChange: (v: T) => void;
}) {
  return (
    <div className="space-y-1">
      <div className="text-sm text-slate-200">{label}</div>
      {hint && <div className="text-xs text-slate-400">{hint}</div>}
      <div className="flex flex-wrap gap-1">
        {options.map(([v, l]) => (
          <button
            key={v}
            type="button"
            className={`rounded px-2 py-1 text-xs ${value === v ? "bg-sky-700 text-white" : "bg-slate-800 text-slate-300 hover:bg-slate-700"}`}
            onClick={() => onChange(v)}
          >
            {l}
          </button>
        ))}
      </div>
    </div>
  );
}

const LEVELS: Opt<"off" | "soft" | "hard"> = [
  ["off", "Farketmez"],
  ["soft", "Mümkünse"],
  ["hard", "Kesin kural"],
];
const WEEKEND: Opt<"work" | "avoid" | "off"> = [
  ["work", "Çalışılır"],
  ["avoid", "Mümkünse çalışma"],
  ["off", "Çalışılmaz"],
];

export function PrefsPanel({
  value,
  onChange,
  defaults,
  showStart = true,
}: {
  value: PlanPrefs;
  onChange: (p: PlanPrefs) => void;
  defaults: PrefsDefaults | null;
  showStart?: boolean;
}) {
  const [closedDraft, setClosedDraft] = useState("");
  const set = <K extends keyof PlanPrefs>(k: K, v: PlanPrefs[K]) => onChange({ ...value, [k]: v });
  const toggleId = (k: "exclude_machine_ids" | "exclude_mold_ids", id: number) => {
    const cur = new Set(value[k]);
    if (cur.has(id)) cur.delete(id);
    else cur.add(id);
    set(k, Array.from(cur).sort((a, b) => a - b));
  };
  const shiftText = defaults?.shifts.map((s) => `${s.name} ${s.start}–${s.end}`).join(" · ");

  return (
    <div className="grid gap-4 rounded border border-slate-700 bg-slate-900/60 p-3 md:grid-cols-2">
      <div className="space-y-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Plan nasıl kurulsun?</div>
        <Choice
          label="Öncelik"
          value={value.priority}
          onChange={(v) => set("priority", v)}
          options={[
            ["due", "Teslim tarihine yetişmek"],
            ["min_changeover", "En az kalıp değişimi"],
            ["shortest", "En kısa toplam süre"],
            ["low_risk", "En düşük risk"],
          ]}
        />
        <Choice
          label="Sipariş bölme"
          hint="Aynı ürünü basan birden fazla kalıp varsa sipariş bölünebilir mi?"
          value={value.split}
          onChange={(v) => set("split", v)}
          options={[
            ["no", "Hayır"],
            ["if_late", "Sadece termine yetişmiyorsa"],
            ["yes", "Evet"],
          ]}
        />
        <Choice
          label="Aynı ürünü birlikte üret"
          hint="Kalıp değiştirmemek için aynı ürünün siparişleri birleştirilsin mi? (termin bozulursa ayrılır)"
          value={value.group_same_product ? "yes" : "no"}
          onChange={(v) => set("group_same_product", v === "yes")}
          options={[
            ["yes", "Evet"],
            ["no", "Hayır"],
          ]}
        />
        <Choice
          label="Riskli makineler"
          hint="Son dönemde sık arıza yapan makineler termini yakın işlerde kullanılmasın mı?"
          value={value.risky_machines}
          onChange={(v) => set("risky_machines", v)}
          options={[
            ["avoid", "Kaçın"],
            ["ignore", "Farketmez"],
          ]}
        />
        <Choice
          label="Stok"
          value={value.stock_allocation ? (value.warn_stock_zero ? "warn" : "share") : "off"}
          onChange={(v) => onChange({ ...value, stock_allocation: v !== "off", warn_stock_zero: v === "warn" })}
          options={[
            ["warn", "Siparişlere paylaştır, sıfırlanırsa uyar"],
            ["share", "Paylaştır, uyarma"],
            ["off", "Her siparişe ayrı bak"],
          ]}
        />
        <Choice
          label="Çevrim süresi"
          value={value.cycle_source}
          onChange={(v) => set("cycle_source", v)}
          options={[
            ["history", "Geçmiş gerçek çevrim"],
            ["target", "Kalıp hedef çevrimi"],
          ]}
        />
        <Choice
          label="Kalıp değişim süresi"
          value={value.changeover ? "yes" : "no"}
          onChange={(v) => set("changeover", v === "yes")}
          options={[
            ["yes", "Hesaba kat (sök + tak dk)"],
            ["no", "Yok say"],
          ]}
        />
        {showStart && (
          <label className="block space-y-1 text-sm text-slate-200">
            <span>Başlangıç</span>
            <input
              type="datetime-local"
              className="block rounded bg-slate-800 px-2 py-1 text-sm"
              value={value.start_at ? value.start_at.slice(0, 16) : ""}
              onChange={(e) => set("start_at", e.target.value || null)}
            />
            <span className="block text-xs text-slate-400">Boş = şimdi</span>
          </label>
        )}
      </div>

      <div className="space-y-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Takvim ve vardiya</div>
        <Choice
          label="Çalışma düzeni"
          hint={shiftText ? `Vardiyalar: ${shiftText}` : undefined}
          value={value.work_pattern}
          onChange={(v) => set("work_pattern", v)}
          options={[
            ["3x8", "3 vardiya (7/24)"],
            ["2x8", "2 vardiya"],
            ["1x8", "Tek vardiya"],
          ]}
        />
        <Choice label="Cumartesi" value={value.saturday} onChange={(v) => set("saturday", v)} options={WEEKEND} />
        <Choice label="Pazar" value={value.sunday} onChange={(v) => set("sunday", v)} options={WEEKEND} />
        <Choice
          label="Manuel kalıpları gece vardiyasına koyma"
          value={value.manual_not_night}
          onChange={(v) => set("manual_not_night", v)}
          options={LEVELS}
        />
        <div className="space-y-1">
          <Choice
            label="Kalıp değişimlerini gündüze planla"
            value={value.changeover_daytime}
            onChange={(v) => set("changeover_daytime", v)}
            options={LEVELS}
          />
          {value.changeover_daytime !== "off" && (
            <input
              className="w-32 rounded bg-slate-800 px-2 py-1 text-xs"
              value={value.changeover_window}
              onChange={(e) => set("changeover_window", e.target.value)}
              placeholder="08:00-17:00"
            />
          )}
        </div>
        <div className="space-y-1">
          <div className="text-sm text-slate-200">Kapalı günler (tatil)</div>
          <div className="flex flex-wrap items-center gap-1">
            {value.closed_days.map((d) => (
              <button
                key={d}
                type="button"
                className="rounded bg-slate-700 px-2 py-1 text-xs"
                title="Kaldır"
                onClick={() => set("closed_days", value.closed_days.filter((x) => x !== d))}
              >
                {d} ✕
              </button>
            ))}
            <input
              type="date"
              className="rounded bg-slate-800 px-2 py-1 text-xs"
              value={closedDraft}
              onChange={(e) => setClosedDraft(e.target.value)}
            />
            <button
              type="button"
              className="rounded bg-slate-700 px-2 py-1 text-xs disabled:opacity-40"
              disabled={!closedDraft}
              onClick={() => {
                set("closed_days", Array.from(new Set([...value.closed_days, closedDraft])).sort());
                setClosedDraft("");
              }}
            >
              Ekle
            </button>
          </div>
        </div>
        <p className="rounded bg-slate-800/70 px-2 py-1 text-xs text-slate-400">
          «Kesin kural» o saatleri plandan çıkarır. «Mümkünse» tercih edilir; termine yetişmek gerekirse esnetilir ve
          plan sonucunda açıkça yazılır.
        </p>
        {defaults && defaults.machines.length > 0 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-slate-300">
              Hariç makine / kalıp ({value.exclude_machine_ids.length + value.exclude_mold_ids.length})
            </summary>
            <div className="mt-2 flex flex-wrap gap-1">
              {defaults.machines.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className={`rounded px-2 py-1 text-xs ${value.exclude_machine_ids.includes(m.id) ? "bg-red-800 text-white line-through" : "bg-slate-800 text-slate-300"}`}
                  onClick={() => toggleId("exclude_machine_ids", m.id)}
                >
                  {m.name}
                </button>
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {defaults.molds.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className={`rounded px-2 py-1 text-xs ${value.exclude_mold_ids.includes(m.id) ? "bg-red-800 text-white line-through" : "bg-slate-800 text-slate-300"}`}
                  onClick={() => toggleId("exclude_mold_ids", m.id)}
                >
                  {m.name}
                  {m.work_mode === "manual" ? " (manuel)" : ""}
                </button>
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}
