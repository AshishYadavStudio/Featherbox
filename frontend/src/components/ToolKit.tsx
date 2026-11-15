import { useState } from "react";
import { Loader2, X } from "lucide-react";
import { post, waitForJob, type Job } from "../api";

/** Run a GPU job (image or video) and follow its progress. Returns the finished job (or throws). */
export function useJob() {
  const [job, setJob] = useState<Job | null>(null);
  const [running, setRunning] = useState(false);

  async function run(path: string, body: unknown): Promise<Job> {
    setRunning(true);
    try {
      const j = await post<Job>(path, body);
      setJob(j);
      const final = await waitForJob(j.id, setJob);
      if (final.status === "error") throw new Error(final.error || "Something went wrong");
      return final;
    } finally {
      setRunning(false);
    }
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
