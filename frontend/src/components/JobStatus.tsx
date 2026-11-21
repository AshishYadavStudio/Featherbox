import { useEffect, useRef, useState } from "react";
import { Bell, Check, Clock, Loader2, X } from "lucide-react";
import { api, save, type Job } from "../api";
import type { Page } from "../App";

/** What each kind of job is called in the UI, and which page shows it. */
const KINDS: Record<string, { label: string; done: string; page: Page }> = {
  script: { label: "Writing your script", done: "Your script is ready", page: "studio" },
  render: { label: "Making your video", done: "Your video is ready", page: "studio" },
  scene: { label: "Updating a scene", done: "Scene updated", page: "studio" },
  music: { label: "Changing the music", done: "Music updated", page: "studio" },
  image: { label: "Painting", done: "Your pictures are ready", page: "image" },
  video: { label: "Making a video clip", done: "Your clip is ready", page: "video" },
  transcribe: { label: "Transcribing", done: "Your transcript is ready", page: "transcribe" },
};
const kindOf = (j: Job) => KINDS[j.kind] ?? { label: j.title, done: "Finished", page: "studio" as Page };
const detail = (j: Job) => j.title.replace(/^[^:]{1,20}:\s*/, "");   // "Video: Octopuses…" -> "Octopuses…"
const isActive = (j: Job) => j.status === "queued" || j.status === "running";

const canNotify = () => typeof Notification !== "undefined";

/**
 * Sidebar status of background jobs, on every page:
 * - what is running (and what waits behind it), with progress; click to open it
 * - progress in the window title, so it shows in the taskbar
 * - a Windows notification when a job finishes while Featherbox isn't in front (after the user allows it once)
 * - hands a finished script/video to Studio if Studio wasn't open to receive it
 */
export function JobStatus({ page, go, openProject }: {
  page: Page; go: (p: Page) => void; openProject: (pid: string) => void;
}) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [finished, setFinished] = useState<Job[]>([]);           // jobs that ended while we watched, newest first
  const [perm, setPerm] = useState(() => (canNotify() ? Notification.permission : "denied"));
  const [unseen, setUnseen] = useState(false);                      // finished while the window was in the background
  const seen = useRef<Map<string, Job["status"]> | null>(null);
  const pageRef = useRef(page);
  pageRef.current = page;

  const open = (j: Job) => {
    const k = kindOf(j);
    if ((j.kind === "scene" || j.kind === "music") && j.ref) openProject(j.ref.split(":")[0]);
    else if (j.kind === "render" && j.status === "done" && j.result?.project) openProject(j.result.project);
    else go(k.page);
    dismiss(j);
  };
  const dismiss = (j: Job) => setFinished((f) => f.filter((x) => x.id !== j.id));

  // poll the job list: quickly while something runs, slowly otherwise (and keep going if the backend blips)
  const active = jobs.filter(isActive);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const list = await api<Job[]>("/api/jobs").catch(() => null);
      if (!alive) return;
      if (list) {
        const before = seen.current;
        if (before) {
          for (const j of [...list].reverse()) {   // oldest first, so the newest note ends up on top
            const was = before.get(j.id);
            if ((was === "queued" || was === "running") && !isActive(j)) ended(j);
          }
        }
        seen.current = new Map(list.map((j) => [j.id, j.status]));
        setJobs(list);
      }
      timer = setTimeout(poll, list?.some(isActive) ? 1500 : 4000);
    };
    poll();
    return () => { alive = false; clearTimeout(timer); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function ended(j: Job) {
    // Studio only picks up results while it is open; keep a finished script or video for it otherwise
    if (pageRef.current !== "studio" && j.status === "done") {
      if (j.kind === "script" && j.result) { save("featherbox.script", j.result); save("featherbox.stage", "edit"); }
      if (j.kind === "render" && j.result) { save("featherbox.result", j.result); save("featherbox.stage", "done"); }
    }
    if (j.status === "cancelled") return;
    const away = document.hidden || !document.hasFocus();
    // no "it's ready" note for the page you are looking at: it already shows the result
    if (away || pageRef.current !== kindOf(j).page) setFinished((f) => [j, ...f.filter((x) => x.id !== j.id)].slice(0, 3));
    if (away) setUnseen(true);
    if (away && canNotify() && Notification.permission === "granted") {
      const k = kindOf(j);
      const n = new Notification(j.status === "done" ? k.done : `${k.label} failed`, {
        body: j.status === "done" ? detail(j) : (j.error || detail(j)), tag: j.id,
      });
      n.onclick = () => { window.focus(); open(j); n.close(); };
    }
  }

  // window title: progress while running, a tick when something finished in the background
  const run = active.find((j) => j.status === "running");
  const queued = active.filter((j) => j.status === "queued").length;
  useEffect(() => {
    if (run) document.title = `${Math.round(run.progress * 100)}% · ${kindOf(run).label} — Featherbox`;
    else if (active.length) document.title = "Waiting… — Featherbox";
    else if (unseen && finished[0]) document.title = finished[0].status === "done"
      ? `✓ ${kindOf(finished[0]).done} — Featherbox` : "✕ Something failed — Featherbox";
    else document.title = "Featherbox";
  }, [run?.id, run?.progress, active.length, unseen, finished]);
  useEffect(() => {
    const back = () => setUnseen(false);
    window.addEventListener("focus", back);
    return () => window.removeEventListener("focus", back);
  }, []);
  // finished notes go away by themselves two minutes after you are back (not while you are away from the window)
  useEffect(() => {
    if (!finished.length || unseen) return;
    const t = setTimeout(() => setFinished([]), 120000);
    return () => clearTimeout(t);
  }, [finished, unseen]);

  const ask = async () => { if (canNotify()) setPerm(await Notification.requestPermission()); };

  if (!active.length && !finished.length) return null;
  return (
    <div className="job-status">
      {active.length > 0 && (
        <>
          <button className="job-status-main" onClick={() => open(run ?? active[0])} title={`${detail(run ?? active[0])}\nClick to open`}>
            {run ? <Loader2 size={15} className="spin" /> : <Clock size={15} />}
            <div className="job-status-text">
              <div className="job-status-label">{run ? kindOf(run).label : "Waiting for the GPU"}</div>
              <div className="job-status-stage">{run ? run.stage : detail(active[0])}</div>
            </div>
            {run && <span className="job-status-pct">{Math.round(run.progress * 100)}%</span>}
          </button>
          {run && <div className="bar"><div style={{ width: `${run.progress * 100}%` }} /></div>}
          {queued > 0 && <div className="job-status-more">{queued} more waiting</div>}
          {perm === "default" && (
            <button className="link job-status-notify" onClick={ask}><Bell size={12} /> Tell me when it's done</button>
          )}
        </>
      )}
      {finished.map((f) => (
        <div key={f.id} className={`job-status-main finished ${f.status}`}>
          {f.status === "done" ? <Check size={15} /> : <X size={15} />}
          <button className="job-status-text" onClick={() => open(f)} title={f.error || detail(f)}>
            <div className="job-status-label">{f.status === "done" ? kindOf(f).done : `${kindOf(f).label} failed`}</div>
            <div className="job-status-stage">{f.status === "done" ? "Click to open" : (f.error || detail(f))}</div>
          </button>
          <button className="icon-btn" title="Dismiss" onClick={() => dismiss(f)}><X size={13} /></button>
        </div>
      ))}
    </div>
  );
}
