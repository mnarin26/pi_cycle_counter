import { useEffect, useState } from "react";
import { apiGet } from "../api/client";

type Activity = {
  id: number;
  created_at: string | null;
  actor_name: string;
  action: string;
  text: string;
};

export function EventsPage() {
  const [rows, setRows] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGet<Activity[]>("/api/activity?limit=300")
      .then(setRows)
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Olaylar</h2>
      <div className="overflow-x-auto rounded border border-slate-700 text-sm">
        <table className="w-full">
          <thead className="bg-panel2 text-left">
            <tr>
              <th className="p-2 w-48">Zaman</th>
              <th className="p-2">İşlem</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id} className="border-t border-slate-800">
                <td className="p-2 text-slate-400">
                  {e.created_at
                    ? new Date(e.created_at).toLocaleString("tr-TR", { timeZone: "Europe/Istanbul" })
                    : "—"}
                </td>
                <td className="p-2">{e.text}</td>
              </tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr>
                <td className="p-3 text-slate-500" colSpan={2}>
                  Henüz kullanıcı işlemi yok
                </td>
              </tr>
            )}
            {loading && (
              <tr>
                <td className="p-3 text-slate-500" colSpan={2}>
                  Yükleniyor…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
