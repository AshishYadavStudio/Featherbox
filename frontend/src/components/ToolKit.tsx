import { useEffect, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import { activeJob, post, waitForJob, type Job } from "../api";

/** Run a job (image, video, transcribe…) and follow its progress. Returns the finished job (or throws).
 *
 * Jobs keep running in the app when you switch pages. When the page opens again, a job of this `kind` that is
 * still going is picked up again (progress bar and all), and `onResumedEnd` gets it once it finishes. */
export function useJob(kind?: string, onResumedEnd?: (final: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const [running, setRunning] = useState(false);
  const alive = useRef(true);
  const endRef = useRef(onResumedEnd);
  endRef.current = onResumedEnd;

  async function follow(j: Job): Promise<Job> {
    setJob(j);
    setRunning(true);
    try {
      return await waitForJob(j.id, setJob, () => alive.current);
    } finally {
      if (alive.current) setRunning(false);
    }
  }

  useEffect(() => {
    alive.current = true;
    if (kind) {
      activeJob([kind]).then((j) => {
        if (j && alive.current) follow(j).then((f) => endRef.current?.(f)).catch(() => {});
      });
    }
    return () => { alive.current = false; };
  }, [kind]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(path: string, body: unknown): Promise<Job> {
    setRunning(true);
    const j = await post<Job>(path, body).catch((e) => { setRunning(false); throw e; });
    const final = await follow(j);
    if (final.status === "error") throw new Error(final.error || "Something went wrong");
    if (final.status === "cancelled") throw new Error("Cancelled");
    return final;
  }

  const cancel = () => job && post(`/api/jobs/${job.id}/cancel`).catch(() => {});
  return { job, running, run, cancel };
}

export function JobProgress({ job, onCancel, queued = "Waiting for the GPU (another job is running)…" }: {
  job: Job | null; onCancel: () => void; queued?: string;
}) {
  const pct = job ? Math.round(job.progress * 100) : 0;
  const stage = !job ? "Starting…" : job.status === "queued" ? queued : job.stage;
  return (
    <div className="tool-progress">
      <div className="row between">
        <span className="small"><Loader2 size={14} className="spin" /> {stage}</span>
        <button className="icon-btn" title="Cancel" onClick={onCancel}><X size={15} /></button>
      </div>
      <div className="bar"><div style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

export const ago = (t: number) => {
  const s = Math.max(1, Math.round(Date.now() / 1000 - t));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
};

export const fmtSecs = (s?: number) => (s == null ? "" : s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.round(s)} s`);

export function Segmented<T extends string | number>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void; options: [T, string][];
}) {
  return (
    <div className="segmented">
      {options.map(([v, l]) => (
        <button key={String(v)} className={value === v ? "on" : ""} onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  );
}
