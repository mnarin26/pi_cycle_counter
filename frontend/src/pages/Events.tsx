import { useEffect, useState } from "react";
import { apiGet } from "../api/client";

type Ev = { id: number; type: string; machine_id: number | null; payload: string | null; created_at: string | null };

export function EventsPage() {
  const [rows, setRows] = useState<Ev[]>([]);

  useEffect(() => {
    apiGet<Ev[]>("/api/events?limit=300").then(setRows);
  }, []);

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Olaylar</h2>
      <div className="overflow-x-auto rounded border border-slate-700 text-sm">
        <table className="w-full">
          <thead className="bg-panel2 text-left">
            <tr>
              <th className="p-2">Zaman</th>
              <th className="p-2">Tip</th>
              <th className="p-2">Makine</th>
              <th className="p-2">Yük</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id} className="border-t border-slate-800">
                <td className="p-2 text-slate-400">{e.created_at}</td>
                <td className="p-2">{e.type}</td>
                <td className="p-2">{e.machine_id ?? "—"}</td>
                <td className="p-2 max-w-md truncate">{e.payload}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
