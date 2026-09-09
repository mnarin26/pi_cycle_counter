import { useCallback, useEffect, useMemo, useState } from "react";
import { datetimeLocalInputToUtcIso } from "../lib/chartTheme";
import { apiDelete, apiDownloadCsv, apiGet, apiPatch, apiPost } from "../api/client";

type Mold = {
  id: number;
  name: string | null;
  qr_code: string | null;
  status: string;
  avg_cycle_s: number;
  target_cycle_s: number | null;
  daily_target_count: number | null;
  work_mode: "auto" | "manual";
  mount_minutes: number | null;
  removal_minutes: number | null;
  created_at?: string | null;
};

type MoldUsageRow = {
  mold_id: number;
  mold_name: string;
  status: string;
  total_cycles: number;
  avg_cycle_s: number;
  machines: Array<{
    machine_id: number;
    machine_name: string;
    cycle_count: number;
  }>;
};

type MoldUsageResponse = {
  range: string;
  from: string;
  to: string;
  rows: MoldUsageRow[];
};

type EditDraft = {
  name: string;
  qr_code: string;
  target_cycle_s: string;
  daily_target_count: string;
  work_mode: "auto" | "manual";
  mount_minutes: string;
  removal_minutes: string;
};

type SortField = "qr" | "daily_target" | "name";
type SortDir = "asc" | "desc";

function localeText(value: string | null | undefined): string {
  return (value ?? "").trim().toLocaleLowerCase("tr-TR");
}

function qrSortParts(qr: string | null): { num: number; str: string } {
  const str = localeText(qr);
  if (/^\d+$/.test(str)) return { num: parseInt(str, 10), str };
  return { num: Number.POSITIVE_INFINITY, str };
}

function compareMolds(a: Mold, b: Mold, field: SortField, dir: SortDir): number {
  let cmp = 0;
  let aMissing = false;
  let bMissing = false;
  if (field === "qr") {
    aMissing = !a.qr_code?.trim();
    bMissing = !b.qr_code?.trim();
    if (!aMissing && !bMissing) {
      const qa = qrSortParts(a.qr_code);
      const qb = qrSortParts(b.qr_code);
      cmp = qa.num - qb.num;
      if (cmp === 0) cmp = qa.str.localeCompare(qb.str, "tr");
    }
  } else if (field === "daily_target") {
    aMissing = a.daily_target_count == null;
    bMissing = b.daily_target_count == null;
    if (!aMissing && !bMissing) cmp = (a.daily_target_count as number) - (b.daily_target_count as number);
  } else {
    aMissing = !a.name?.trim();
    bMissing = !b.name?.trim();
    if (!aMissing && !bMissing) cmp = localeText(a.name).localeCompare(localeText(b.name), "tr");
  }
  if (aMissing !== bMissing) return aMissing ? 1 : -1;
  if (cmp === 0) cmp = a.id - b.id;
  return dir === "asc" ? cmp : -cmp;
}

function draftFromMold(m: Mold): EditDraft {
  return {
    name: m.name ?? "",
    qr_code: m.qr_code ?? "",
    target_cycle_s: m.target_cycle_s != null ? String(m.target_cycle_s) : "",
    daily_target_count: m.daily_target_count != null ? String(m.daily_target_count) : "",
    work_mode: m.work_mode === "manual" ? "manual" : "auto",
    mount_minutes: m.mount_minutes != null ? String(m.mount_minutes) : "",
    removal_minutes: m.removal_minutes != null ? String(m.removal_minutes) : "",
  };
}

function MoldCard({
  mold,
  onChanged,
}: {
  mold: Mold;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<EditDraft>(() => draftFromMold(mold));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!editing) setDraft(draftFromMold(mold));
  }, [mold, editing]);

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      let target_cycle_s: number | null = null;
      if (draft.target_cycle_s.trim()) {
        const target = parseFloat(draft.target_cycle_s);
        if (!Number.isFinite(target) || target <= 0) throw new Error("Hedef süre geçerli bir sayı olmalı");
        target_cycle_s = target;
      }
      let daily_target_count: number | null = null;
      if (draft.daily_target_count.trim()) {
        const daily = parseInt(draft.daily_target_count, 10);
        if (!Number.isFinite(daily) || daily <= 0) throw new Error("Günlük hedef geçerli bir sayı olmalı");
        daily_target_count = daily;
      }
      const mount_minutes = draft.mount_minutes.trim() ? parseInt(draft.mount_minutes, 10) : null;
      if (mount_minutes != null && (!Number.isFinite(mount_minutes) || mount_minutes < 0))
        throw new Error("Montaj süresi geçerli bir sayı olmalı");
      const removal_minutes = draft.removal_minutes.trim() ? parseInt(draft.removal_minutes, 10) : null;
      if (removal_minutes != null && (!Number.isFinite(removal_minutes) || removal_minutes < 0))
        throw new Error("Sökme süresi geçerli bir sayı olmalı");
      await apiPatch<Mold>(`/api/molds/${mold.id}`, {
        name: draft.name.trim() || null,
        qr_code: draft.qr_code.trim() || null,
        status: "active",
        target_cycle_s,
        daily_target_count,
        work_mode: draft.work_mode,
        mount_minutes,
        removal_minutes,
      });
      setEditing(false);
      onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    const label = mold.name || `Kalıp #${mold.id}`;
    if (!confirm(`"${label}" silinsin mi?\n\nGeçmiş döngülerde kalıp bağlantısı kaldırılır.`)) return;
    setBusy(true);
    setErr(null);
    try {
      await apiDelete(`/api/molds/${mold.id}`);
      onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded border border-slate-700 bg-panel2 p-4">
      {!editing ? (
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-[200px] flex-1">
            <div className="font-medium">{mold.name || "İsimsiz kalıp"}</div>
            <div className="text-xs text-slate-400">
              QR: {mold.qr_code ? <code className="text-accent">{mold.qr_code}</code> : "—"}
              {mold.target_cycle_s != null ? ` · Hedef ${mold.target_cycle_s.toFixed(2)}s` : ""}
              {mold.daily_target_count != null ? ` · Günlük hedef ${mold.daily_target_count}` : ""}
              {` · ${mold.work_mode === "manual" ? "Manuel" : "Otomatik"}`}
            </div>
          </div>
          <button
            type="button"
            className="min-h-[44px] rounded bg-slate-600 px-3 text-sm disabled:opacity-50"
            disabled={busy}
            onClick={() => setEditing(true)}
          >
            Düzenle
          </button>
          <button
            type="button"
            className="min-h-[44px] rounded bg-red-900/80 px-3 text-sm text-red-100 disabled:opacity-50"
            disabled={busy}
            onClick={() => void remove()}
          >
            Sil
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="text-sm sm:col-span-2">
              <span className="mb-1 block text-xs text-slate-400">Ad</span>
              <input
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                value={draft.name}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              />
            </label>
            <label className="text-sm sm:col-span-2">
              <span className="mb-1 block text-xs text-slate-400">QR kodu (MOLD:042 plakasındaki kod)</span>
              <input
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2 font-mono"
                placeholder="042"
                value={draft.qr_code}
                onChange={(e) => setDraft((d) => ({ ...d, qr_code: e.target.value }))}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs text-slate-400">Hedef çalışma süresi (s)</span>
              <input
                type="number"
                step="0.01"
                min="0.1"
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                placeholder="örn. 12.50"
                value={draft.target_cycle_s}
                onChange={(e) => setDraft((d) => ({ ...d, target_cycle_s: e.target.value }))}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs text-slate-400">Günlük hedef baskı</span>
              <input
                type="number"
                step="1"
                min="1"
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                placeholder="örn. 15000"
                value={draft.daily_target_count}
                onChange={(e) => setDraft((d) => ({ ...d, daily_target_count: e.target.value }))}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs text-slate-400">Çalışma modu</span>
              <select
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                value={draft.work_mode}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, work_mode: e.target.value as "auto" | "manual" }))
                }
              >
                <option value="auto">Otomatik</option>
                <option value="manual">Manuel</option>
              </select>
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs text-slate-400">Montaj süresi (dk)</span>
              <input
                type="number"
                step="1"
                min="0"
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                placeholder="örn. 30"
                value={draft.mount_minutes}
                onChange={(e) => setDraft((d) => ({ ...d, mount_minutes: e.target.value }))}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs text-slate-400">Sökme süresi (dk)</span>
              <input
                type="number"
                step="1"
                min="0"
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                placeholder="örn. 20"
                value={draft.removal_minutes}
                onChange={(e) => setDraft((d) => ({ ...d, removal_minutes: e.target.value }))}
              />
            </label>
          </div>
          <p className="rounded border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
            Günlük hedef girilirse verimlilik ona göre (vardiya saatine oranlı) hesaplanır; boşsa hedef
            çalışma süresi kullanılır. Manuel kalıpta vardiya mola süreleri; her kalıp değişiminde eski
            kalıbın sökme + yeni kalıbın montaj süresi verimlilik süresinden düşülür.
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded bg-accent px-3 py-2 text-sm text-panel disabled:opacity-50"
              disabled={busy}
              onClick={() => void save()}
            >
              {busy ? "Kaydediliyor…" : "Kaydet"}
            </button>
            <button
              type="button"
              className="rounded bg-slate-700 px-3 py-2 text-sm disabled:opacity-50"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setErr(null);
              }}
            >
              İptal
            </button>
          </div>
        </div>
      )}
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </div>
  );
}

function CreateMoldForm({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [qrCode, setQrCode] = useState("");
  const [targetCycleS, setTargetCycleS] = useState("");
  const [dailyTarget, setDailyTarget] = useState("");
  const [workMode, setWorkMode] = useState<"auto" | "manual">("auto");
  const [mountMinutes, setMountMinutes] = useState("");
  const [removalMinutes, setRemovalMinutes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const target = parseFloat(targetCycleS);
      if (targetCycleS.trim() && (!Number.isFinite(target) || target <= 0)) {
        throw new Error("Hedef çalışma süresi geçerli bir sayı olmalı");
      }
      let daily_target_count: number | undefined;
      if (dailyTarget.trim()) {
        const daily = parseInt(dailyTarget, 10);
        if (!Number.isFinite(daily) || daily <= 0) throw new Error("Günlük hedef geçerli bir sayı olmalı");
        daily_target_count = daily;
      }
      let mount_minutes: number | undefined;
      if (mountMinutes.trim()) {
        const m = parseInt(mountMinutes, 10);
        if (!Number.isFinite(m) || m < 0) throw new Error("Montaj süresi geçerli bir sayı olmalı");
        mount_minutes = m;
      }
      let removal_minutes: number | undefined;
      if (removalMinutes.trim()) {
        const r = parseInt(removalMinutes, 10);
        if (!Number.isFinite(r) || r < 0) throw new Error("Sökme süresi geçerli bir sayı olmalı");
        removal_minutes = r;
      }
      await apiPost<Mold>("/api/molds", {
        name: name.trim(),
        qr_code: qrCode.trim(),
        target_cycle_s: targetCycleS.trim() ? target : undefined,
        daily_target_count,
        work_mode: workMode,
        mount_minutes,
        removal_minutes,
      });
      setOpen(false);
      setName("");
      setQrCode("");
      setTargetCycleS("");
      setDailyTarget("");
      setWorkMode("auto");
      setMountMinutes("");
      setRemovalMinutes("");
      onCreated();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        className="mb-4 rounded bg-emerald-700 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-600"
        onClick={() => setOpen(true)}
      >
        + Yeni Kalıp
      </button>
    );
  }

  return (
    <div className="mb-4 rounded border border-emerald-800 bg-emerald-950/30 p-4">
      <h3 className="mb-3 text-lg font-semibold text-emerald-300">Yeni Kalıp Oluştur</h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="text-sm sm:col-span-2">
          <span className="mb-1 block text-xs text-slate-400">Kalıp adı *</span>
          <input
            className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Kapak A"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-slate-400">QR kodu *</span>
          <input
            className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2 font-mono"
            value={qrCode}
            onChange={(e) => setQrCode(e.target.value)}
            placeholder="042"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-slate-400">Hedef çalışma süresi (sn)</span>
          <input
            type="number"
            step="0.01"
            min="0.1"
            className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
            value={targetCycleS}
            onChange={(e) => setTargetCycleS(e.target.value)}
            placeholder="Opsiyonel — verimlilik için önerilir"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-slate-400">Günlük hedef baskı</span>
          <input
            type="number"
            step="1"
            min="1"
            className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
            value={dailyTarget}
            onChange={(e) => setDailyTarget(e.target.value)}
            placeholder="15000"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-slate-400">Çalışma modu</span>
          <select
            className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
            value={workMode}
            onChange={(e) => setWorkMode(e.target.value as "auto" | "manual")}
          >
            <option value="auto">Otomatik</option>
            <option value="manual">Manuel</option>
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-slate-400">Montaj süresi (dk)</span>
          <input
            type="number"
            step="1"
            min="0"
            className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
            value={mountMinutes}
            onChange={(e) => setMountMinutes(e.target.value)}
            placeholder="örn. 30"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-slate-400">Sökme süresi (dk)</span>
          <input
            type="number"
            step="1"
            min="0"
            className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
            value={removalMinutes}
            onChange={(e) => setRemovalMinutes(e.target.value)}
            placeholder="örn. 20"
          />
        </label>
      </div>
      <p className="mt-2 text-xs text-slate-500">
        Hedef süre ve günlük hedef, bilgi ekranında verimlilik ve gerçekleşme oranı hesabında kullanılır.
        Günlük hedef girilirse öncelikli olup vardiya saatine oranlı bölünür. Manuel kalıpta vardiya
        mola süreleri, her kalıp değişiminde sökme + montaj süreleri verimlilikten düşülür.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          disabled={busy}
          onClick={() => void submit()}
        >
          {busy ? "Kaydediliyor…" : "Oluştur"}
        </button>
        <button
          type="button"
          className="rounded bg-slate-700 px-4 py-2 text-sm"
          disabled={busy}
          onClick={() => {
            setOpen(false);
            setErr(null);
          }}
        >
          İptal
        </button>
      </div>
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </div>
  );
}

export function MoldsPage() {
  const [rows, setRows] = useState<Mold[]>([]);
  const [usage, setUsage] = useState<MoldUsageResponse | null>(null);
  const [range, setRange] = useState<"daily" | "weekly" | "monthly" | "yearly">("weekly");
  const [fromInput, setFromInput] = useState("");
  const [toInput, setToInput] = useState("");
  const [loadingMolds, setLoadingMolds] = useState(true);
  const [loadingUsage, setLoadingUsage] = useState(false);
  const [exportBusy, setExportBusy] = useState<{ moldId: number; kind: "summary" | "cycles" } | null>(
    null,
  );
  const [err, setErr] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sortField, setSortField] = useState<SortField>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const filteredMolds = useMemo(() => {
    const q = localeText(search);
    const matched = q
      ? rows.filter((m) => {
          if (localeText(m.name).includes(q) || localeText(m.qr_code).includes(q)) return true;
          if (/^\d+$/.test(q) && m.qr_code && /^\d+$/.test(m.qr_code.trim())) {
            return parseInt(q, 10) === parseInt(m.qr_code.trim(), 10);
          }
          return false;
        })
      : rows.slice();
    matched.sort((a, b) => compareMolds(a, b, sortField, sortDir));
    return matched;
  }, [rows, search, sortField, sortDir]);

  const usageQuery = useCallback(() => {
    const p = new URLSearchParams({ range });
    if (fromInput) p.set("from", datetimeLocalInputToUtcIso(fromInput));
    if (toInput) p.set("to", datetimeLocalInputToUtcIso(toInput));
    return p.toString();
  }, [range, fromInput, toInput]);

  const loadMolds = useCallback(async () => {
    setLoadingMolds(true);
    try {
      const molds = await apiGet<Mold[]>("/api/molds");
      setRows(molds);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoadingMolds(false);
    }
  }, []);

  const loadUsage = useCallback(async () => {
    setLoadingUsage(true);
    try {
      const usageResp = await apiGet<MoldUsageResponse>(`/api/molds/usage?${usageQuery()}`);
      setUsage(usageResp);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoadingUsage(false);
    }
  }, [usageQuery]);

  const reloadAll = useCallback(async () => {
    await Promise.all([loadMolds(), loadUsage()]);
  }, [loadMolds, loadUsage]);

  const downloadMoldExport = useCallback(
    async (moldId: number, kind: "summary" | "cycles") => {
      setExportBusy({ moldId, kind });
      try {
        const p = new URLSearchParams({ range, kind, mold_id: String(moldId) });
        if (fromInput) p.set("from", datetimeLocalInputToUtcIso(fromInput));
        if (toInput) p.set("to", datetimeLocalInputToUtcIso(toInput));
        await apiDownloadCsv(`/api/molds/export?${p.toString()}`, `kalip_${moldId}_${kind}.csv`);
      } catch (e) {
        setErr(String(e));
      } finally {
        setExportBusy(null);
      }
    },
    [range, fromInput, toInput],
  );

  useEffect(() => {
    void loadMolds();
  }, [loadMolds]);

  useEffect(() => {
    void loadUsage();
  }, [loadUsage]);

  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Kalıplar</h2>
      <CreateMoldForm onCreated={() => void reloadAll()} />
      {err && <p className="mb-3 text-sm text-red-300">{err}</p>}

      {loadingMolds && rows.length === 0 ? (
        <p className="text-sm text-slate-400">Kalıp listesi yükleniyor…</p>
      ) : (
        <div className="space-y-3">
          <div className="mb-1 flex flex-wrap items-end gap-3">
            <label className="min-w-[220px] flex-1 text-sm">
              <span className="mb-1 block text-xs text-slate-400">Ara (isim veya QR)</span>
              <input
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-2"
                placeholder="Kalıp adı veya QR kodu"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs text-slate-400">Sırala</span>
              <select
                className="rounded border border-slate-600 bg-slate-900 px-2 py-2"
                value={sortField}
                onChange={(e) => setSortField(e.target.value as SortField)}
              >
                <option value="name">İsim</option>
                <option value="qr">QR kod</option>
                <option value="daily_target">Hedef baskı adedi</option>
              </select>
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs text-slate-400">Yön</span>
              <select
                className="rounded border border-slate-600 bg-slate-900 px-2 py-2"
                value={sortDir}
                onChange={(e) => setSortDir(e.target.value as SortDir)}
              >
                <option value="asc">Düşükten yükseğe</option>
                <option value="desc">Yüksekten düşüğe</option>
              </select>
            </label>
          </div>
          {filteredMolds.map((m) => (
            <MoldCard key={m.id} mold={m} onChanged={() => void reloadAll()} />
          ))}
          {rows.length === 0 && !loadingMolds && (
            <p className="text-sm text-slate-400">Kayıtlı kalıp yok.</p>
          )}
          {rows.length > 0 && filteredMolds.length === 0 && (
            <p className="text-sm text-slate-400">Aramaya uyan kalıp yok.</p>
          )}
        </div>
      )}

      <div className="relative mt-6 rounded border border-slate-700 bg-panel2 p-4">
        {loadingUsage && (
          <div className="absolute right-3 top-3 text-xs text-sky-300">Üretim detayı yükleniyor…</div>
        )}
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <div className="flex gap-2">
            {(["daily", "weekly", "monthly", "yearly"] as const).map((r) => (
              <button
                key={r}
                type="button"
                className={`rounded px-3 py-2 text-sm ${range === r ? "bg-accent text-panel" : "bg-slate-700"}`}
                onClick={() => setRange(r)}
              >
                {r === "daily" ? "Günlük" : r === "weekly" ? "Haftalık" : r === "monthly" ? "Aylık" : "Yıllık"}
              </button>
            ))}
          </div>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-slate-400">Başlangıç</span>
            <input
              type="datetime-local"
              className="rounded border border-slate-600 bg-slate-900 px-2 py-2"
              value={fromInput}
              onChange={(e) => setFromInput(e.target.value)}
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-slate-400">Bitiş</span>
            <input
              type="datetime-local"
              className="rounded border border-slate-600 bg-slate-900 px-2 py-2"
              value={toInput}
              onChange={(e) => setToInput(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="rounded bg-accent px-3 py-2 text-sm text-panel"
            onClick={() => void loadUsage()}
          >
            Aralığı Uygula
          </button>
        </div>
        <h3 className="mb-3 text-lg font-semibold">Kalıp Bazlı Makine Üretim Detayı</h3>
        <div className="space-y-3">
          {(usage?.rows ?? []).map((r) => (
            <div key={r.mold_id} className="rounded border border-slate-700 bg-slate-900/40 p-3">
              <div className="mb-2 flex flex-wrap items-center gap-3">
                <div className="font-medium">{r.mold_name}</div>
                <div className="text-xs text-slate-400">Toplam Adet: {r.total_cycles}</div>
                <div className="text-xs text-slate-400">Ort. Döngü: {r.avg_cycle_s.toFixed(2)}s</div>
                <div className="ml-auto flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="rounded bg-emerald-800 px-2 py-1 text-xs disabled:opacity-50"
                    disabled={
                      exportBusy?.moldId === r.mold_id && exportBusy.kind === "summary"
                    }
                    onClick={() => void downloadMoldExport(r.mold_id, "summary")}
                  >
                    {exportBusy?.moldId === r.mold_id && exportBusy.kind === "summary"
                      ? "İndiriliyor…"
                      : "Özet CSV"}
                  </button>
                  <button
                    type="button"
                    className="rounded bg-emerald-900 px-2 py-1 text-xs disabled:opacity-50"
                    disabled={
                      exportBusy?.moldId === r.mold_id && exportBusy.kind === "cycles"
                    }
                    onClick={() => void downloadMoldExport(r.mold_id, "cycles")}
                  >
                    {exportBusy?.moldId === r.mold_id && exportBusy.kind === "cycles"
                      ? "İndiriliyor…"
                      : "Döngü CSV"}
                  </button>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-slate-400">
                    <tr>
                      <th className="py-1 pr-3">Makine</th>
                      <th className="py-1 pr-3">Adet</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.machines.map((mRow) => (
                      <tr key={`${r.mold_id}-${mRow.machine_id}`} className="border-t border-slate-800">
                        <td className="py-1 pr-3">{mRow.machine_name}</td>
                        <td className="py-1 pr-3">{mRow.cycle_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
          {usage && usage.rows.length === 0 && !loadingUsage && (
            <p className="text-sm text-slate-400">Seçilen aralıkta kalıp üretim kaydı yok.</p>
          )}
        </div>
      </div>
    </div>
  );
}
