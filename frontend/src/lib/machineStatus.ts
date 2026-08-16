import type { MachineSnap } from "../hooks/useLiveSnapshot";

export const DEFAULT_IDLE_STOPPED_S = 180;

export type MachineStatusView = { label: string; colorClass: string };

/**
 * Live machine status label.
 *
 * OPEN / CLOSED / MOVING are kept as-is (raw clamp zone). The clamp
 * "UNKNOWN" state is disambiguated: it only means "reflector not found"
 * (no position). If the reflector is visible but the machine is idle, we
 * show "Duruyor" instead of a confusing UNKNOWN.
 */
export function machineStatusView(
  m: Pick<MachineSnap, "state" | "position_01" | "idle_s"> | null | undefined,
  idleStoppedSeconds: number = DEFAULT_IDLE_STOPPED_S,
): MachineStatusView {
  const state = m?.state ?? "";
  if (state === "OPEN") return { label: "OPEN", colorClass: "text-ok" };
  if (state === "CLOSED") return { label: "CLOSED", colorClass: "text-accent" };
  if (state === "MOVING") return { label: "MOVING", colorClass: "text-amber-300" };
  if (state === "DISABLED") return { label: "DISABLED", colorClass: "text-slate-500" };

  // UNKNOWN (or unmapped): reflector not found vs simply stopped.
  if (!m || m.position_01 == null) {
    return { label: "UNKNOWN", colorClass: "text-slate-400" };
  }
  const idle = m.idle_s;
  if (idle == null || idle >= idleStoppedSeconds) {
    return { label: "Duruyor", colorClass: "text-red-300" };
  }
  return { label: "MOVING", colorClass: "text-amber-300" };
}
