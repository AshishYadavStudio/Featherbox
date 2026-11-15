import { useEffect, useRef, useState } from "react";
import { Check, Clapperboard, Copy, FolderOpen, Image as ImageIcon, ImagePlus, Loader2, Mic, Music, Pause, Play, Wand2, X } from "lucide-react";
import { api, post, waitForJob, type Job, type ProjectDetail } from "../api";
import MusicPicker from "./MusicPicker";

type Busy = { target: number | "music"; job: Job | null };
type Draft = { narration: string; image_prompt: string };

/** Finished video + per-scene touch-ups (new image, new voice line) + background music. */
export default function ProjectView({ pid, onLoaded, onEdit, onNew, goLibrary }: {
  pid: string; onLoaded?: (p: ProjectDetail) => void; onEdit?: () => void; onNew?: () => void; goLibrary?: () => void;
}) {
  const [p, setP] = useState<ProjectDetail | null>(null);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [music, setMusic] = useState({ value: "none", volume: 0.5 });
  const [busy, setBusy] = useState<Busy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [playing, setPlaying] = useState<number | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);

  async function load() {
    try {
      const d = await api<ProjectDetail>(`/api/projects/${pid}`);
      setP(d);
      setMusic({ value: d.music, volume: d.music_volume });
      onLoaded?.(d);
    } catch (e: any) {
      setError(e.message);
    }
  }
  useEffect(() => { load(); return () => audio.current?.pause(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(target: number | "music", path: string, body: unknown) {
    setError(null);
    setBusy({ target, job: null });
    try {
      const j = await post<Job>(path, body);
      const final = await waitForJob(j.id, (job) => setBusy({ target, job }));
      if (final.status === "error") setError(final.error);
      if (final.status === "done" && typeof target === "number") {
        setDrafts(({ [target]: _, ...rest }) => rest);
      }
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  function toggleAudio(i: number, src: string) {
    audio.current?.pause();
    if (playing === i) { setPlaying(null); return; }
    const a = new Audio(src);
    a.onended = () => setPlaying(null);
    audio.current = a;
    setPlaying(i);
    a.play().catch(() => setPlaying(null));
  }

  if (!p) {
    return error
      ? <div className="banner error"><span>{error}</span></div>
      : <div className="muted"><Loader2 size={16} className="spin" /> Loading project…</div>;
  }

  const s = p.script;
  const caption = `${s.title}\n\n${s.description || ""}\n\n${(s.hashtags || []).map((h) => "#" + h).join(" ")}`;
  const musicChanged = music.value !== p.music || Math.abs(music.volume - p.music_volume) > 0.001;
  const pct = busy?.job ? Math.round(busy.job.progress * 100) : 0;
  const stage = busy?.job ? (busy.job.status === "queued" ? "Waiting for the GPU…" : busy.job.stage) : "Starting…";

  return (
    <div className="project">
      {error && (
        <div className="banner error">
          <span style={{ whiteSpace: "pre-wrap" }}>{error}</span>
          <button className="icon-btn" onClick={() => setError(null)}><X size={16} /></button>
        </div>
      )}
      <div className="done">
        <div className="phone">
          <video key={p.video} src={p.video} controls autoPlay playsInline />
        </div>
        <div className="done-side">
          <div className="eyebrow ok"><Check size={14} /> Video ready · {p.duration}s</div>
          <h2>{s.title}</h2>
          <div className="card caption-box">
            <pre>{caption}</pre>
            <button className="btn small" onClick={() => { navigator.clipboard.writeText(caption); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy caption"}
            </button>
          </div>
          <div className="row wrap">
            <a className="btn primary" href={p.video} download={`${s.title}.mp4`}>Download MP4</a>
            <button className="btn" onClick={() => post(`/api/projects/${p.id}/reveal`)}><FolderOpen size={16} /> Open folder</button>
            {onEdit && <button className="btn" onClick={onEdit}>Edit script &amp; re-render</button>}
            {onNew && <button className="btn ghost" onClick={onNew}><Wand2 size={16} /> New video</button>}
            {goLibrary && <button className="btn ghost" onClick={goLibrary}>Library</button>}
          </div>

          <div className="card music-card">
            <h3><Music size={16} /> Background music</h3>
            <MusicPicker value={music.value} volume={music.volume} tone={s.tone}
              onChange={(value, volume) => setMusic({ value, volume })} />
            {busy?.target === "music" ? (
              <div className="mini-progress"><div className="bar"><div style={{ width: `${pct}%` }} /></div><span className="muted tiny">{stage}</span></div>
            ) : (
              <button className="btn small primary" disabled={!musicChanged || !!busy}
                onClick={() => run("music", `/api/projects/${p.id}/music`, { music: music.value, music_volume: music.volume })}>
                Apply music
              </button>
            )}
          </div>
        </div>
      </div>

      <section className="shots-wrap">
        <div className="row between">
          <h3>Scenes</h3>
          <span className="muted small">Fix one scene without re-rendering the whole video.</span>
        </div>
        <ol className="shots">
          {p.scenes.map((sc) => {
            const i = sc.index;
            const d = drafts[i] ?? { narration: sc.narration, image_prompt: sc.image_prompt };
            const lineChanged = d.narration.trim() !== sc.narration && d.narration.trim().length > 0;
            const promptChanged = d.image_prompt.trim() !== sc.image_prompt && d.image_prompt.trim().length > 0;
            const mine = busy?.target === i;
            const setD = (patch: Partial<Draft>) => setDrafts((all) => ({ ...all, [i]: { ...d, ...patch } }));
            return (
              <li key={i} className={`shot card ${mine ? "is-busy" : ""}`}>
                <div className="shot-img">
                  {sc.clip
                    ? <video key={sc.clip} src={sc.clip} poster={sc.image} muted loop autoPlay playsInline />
                    : <img src={sc.image} alt={`Scene ${i + 1}`} loading="lazy" />}
                  <span className="shot-no">{i + 1}</span>
                  {sc.motion === "ai" && <span className="shot-motion"><Clapperboard size={11} /> AI motion</span>}
                  {mine && (
                    <div className="shot-overlay">
                      <Loader2 size={22} className="spin" />
                      <span>{pct}%</span>
                    </div>
                  )}
                </div>
                <div className="shot-body">
                  <div className="row between">
                    <label className="lbl">Voice line · {sc.duration.toFixed(1)}s</label>
                    <button className="icon-btn" title={playing === i ? "Stop" : "Play this line"} onClick={() => toggleAudio(i, sc.audio)}>
                      {playing === i ? <Pause size={15} /> : <Play size={15} />}
                    </button>
                  </div>
                  <textarea rows={3} value={d.narration} disabled={!!busy} onChange={(e) => setD({ narration: e.target.value })} />
                  <details open={promptChanged || undefined}>
                    <summary className="lbl">Image prompt</summary>
                    <textarea rows={2} className="mono" value={d.image_prompt} disabled={!!busy} onChange={(e) => setD({ image_prompt: e.target.value })} />
                  </details>
                  {mine ? (
                    <div className="mini-progress"><div className="bar"><div style={{ width: `${pct}%` }} /></div><span className="muted tiny">{stage}</span></div>
                  ) : (
                    <div className="row wrap">
                      <button className="btn small" disabled={!!busy} title="Paint a new image for this scene"
                        onClick={() => run(i, `/api/projects/${p.id}/scenes/${i}`, { new_image: true, image_prompt: d.image_prompt, narration: d.narration })}>
                        <ImagePlus size={14} /> {promptChanged ? "Paint new prompt" : "New image"}
                      </button>
                      <button className={`btn small ${lineChanged ? "primary" : ""}`} disabled={!!busy || !lineChanged}
                        title="Record the edited line again"
                        onClick={() => run(i, `/api/projects/${p.id}/scenes/${i}`, { narration: d.narration })}>
                        <Mic size={14} /> Update voice
                      </button>
                      {p.can_animate && (sc.motion === "ai" ? (
                        <>
                          <button className="btn small" disabled={!!busy} title="Make a different AI clip for this scene"
                            onClick={() => run(i, `/api/projects/${p.id}/scenes/${i}`, { motion: "ai" })}>
                            <Clapperboard size={14} /> New motion
                          </button>
                          <button className="btn small ghost" disabled={!!busy} title="Go back to the still picture with zoom & pan"
                            onClick={() => run(i, `/api/projects/${p.id}/scenes/${i}`, { motion: "still" })}>
                            <ImageIcon size={14} /> Make still
                          </button>
                        </>
                      ) : (
                        <button className="btn small" disabled={!!busy} title="Bring this picture to life with the video model"
                          onClick={() => run(i, `/api/projects/${p.id}/scenes/${i}`, { motion: "ai" })}>
                          <Clapperboard size={14} /> Animate
                        </button>
                      ))}
                      {(lineChanged || promptChanged) && (
                        <button className="btn small ghost" disabled={!!busy} onClick={() => setDrafts(({ [i]: _, ...rest }) => rest)}>Undo</button>
                      )}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </section>
    </div>
  );
}
