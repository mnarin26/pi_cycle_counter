import { MachineCard } from "../components/MachineCard";
import { useLiveSnapshot } from "../hooks/useLiveSnapshot";

export function DashboardPage() {
  const { snapshot } = useLiveSnapshot();
  const activeMachines = snapshot.machines.filter((m) => m.state !== "DISABLED");
  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Pano</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {activeMachines.map((m) => (
          <MachineCard key={m.id} m={m} />
        ))}
      </div>
    </div>
  );
}
