import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, Loader2, Play, Plus, RefreshCw, Sparkles, Trash2, Volume2, Wand2, X } from "lucide-react";
import { api, post, save, store, waitForJob, type Job, type Script, type SystemInfo, type Voice } from "../api";
import MusicPicker from "../components/MusicPicker";
import ProjectView from "../components/ProjectView";

type Stage = "idea" | "writing" | "edit" | "rendering" | "done";

type Idea = { topic: string; language: string; tone: string; seconds: number };
type Motion = "still" | "hook" | "all";
type Look = { style: string; voice: string; speed: number; captions: boolean; music: string; music_volume: number; motion: Motion };
/** Measured on an RTX 3050 4 GB with Wan 2.2 5B; shown as a rough guide. */
const MOTION_TIME: Record<string, string> = {
  fast: "takes about 2–3 minutes on a 4 GB GPU",
  best: "takes about 6–7 minutes on a 4 GB GPU",
};
const DEFAULT_LOOK: Look = { style: "cinematic", voice: "af_heart", speed: 1, captions: true, music: "auto", music_volume: 0.5, motion: "still" };

const EXAMPLES = [
  "Why octopuses have three hearts",
  "The untold story of the Konark Sun Temple",
  "3 money habits every student should start today",
  "What would happen if the Moon disappeared",
];

const TONES = ["educational", "storytelling", "motivational", "funny", "mystery", "news"];

const STYLES: { id: string; label: string; swatch: string }[] = [
  { id: "cinematic", label: "Cinematic", swatch: "linear-gradient(135deg,#1d2b53,#b8562f)" },
  { id: "photoreal", label: "Photo", swatch: "linear-gradient(135deg,#5b7f5e,#d9c7a1)" },
  { id: "anime", label: "Anime", swatch: "linear-gradient(135deg,#ff7eb3,#7afcff)" },
  { id: "3d", label: "3D cartoon", swatch: "linear-gradient(135deg,#ffb347,#8e7dff)" },
  { id: "watercolor", label: "Watercolor", swatch: "linear-gradient(135deg,#a8d8ea,#fcbad3)" },
  { id: "comic", label: "Comic", swatch: "linear-gradient(135deg,#ffde03,#e53935)" },
];

export default function Studio({ sys, goModels, goLibrary }: { sys: SystemInfo | null; goModels: () => void; goLibrary: () => void }) {
  const [stage, setStage] = useState<Stage>(() => store<Stage>("featherbox.stage", "idea"));
  const [idea, setIdea] = useState<Idea>(() => store("featherbox.idea", { topic: "", language: "english", tone: "educational", seconds: 45 }));
  const [look, setLook] = useState<Look>(() => ({ ...DEFAULT_LOOK, ...store<Partial<Look>>("featherbox.look", {}) }));
  const [videoQuality, setVideoQuality] = useState(sys?.settings.video_quality || "fast");
  useEffect(() => { if (sys?.settings.video_quality) setVideoQuality(sys.settings.video_quality); }, [sys?.settings.video_quality]);
  const changeVideoQuality = (q: string) => {
    setVideoQuality(q);
    api("/api/settings", { method: "PATCH", body: JSON.stringify({ video_quality: q }) }).catch(() => {});
  };
  const [script, setScript] = useState<Script | null>(() => store<Script | null>("featherbox.script", null));
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<any>(() => store("featherbox.result", null));
  const [error, setError] = useState<string | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  const jobRef = useRef<string | null>(null);

  useEffect(() => save("featherbox.idea", idea), [idea]);
  useEffect(() => save("featherbox.look", look), [look]);
  useEffect(() => save("featherbox.script", script), [script]);
  useEffect(() => save("featherbox.result", result), [result]);
  useEffect(() => save("featherbox.stage", stage === "writing" || stage === "rendering" ? (script ? "edit" : "idea") : stage), [stage, script]);

  useEffect(() => {
    if (sys?.ready.tts) api<Voice[]>("/api/tts/voices").then(setVoices).catch(() => {});
  }, [sys?.ready.tts]);

  // keep voice consistent with the narration language
  useEffect(() => {
    if (!voices.length) return;
    const hindi = (script?.language || idea.language) === "hindi";
    const match = (v: Voice) => (hindi ? v.lang === "hi" : v.lang.startsWith("en"));
    const ok = voices.find((v) => v.id === look.voice && match(v));
    if (!ok) {
      const pick = voices.find(match);
      if (pick) setLook((l) => ({ ...l, voice: pick.id }));
    }
  }, [voices, idea.language, script?.language]); // eslint-disable-line react-hooks/exhaustive-deps

  const ready = sys?.ready;
  const PART_NAMES: Record<string, string> = { llm: "a text model", image: "an image model", tts: "the voice model", ffmpeg: "the video encoder" };
  const missing = ready ? Object.entries(ready).filter(([, v]) => !v).map(([k]) => PART_NAMES[k] ?? k) : [];

  async function runJob(path: string, body: unknown, running: Stage, onDone: (r: any) => void, fallback: Stage) {
    setError(null);
    setStage(running);
    try {
      const j = await post<Job>(path, body);
      jobRef.current = j.id;
      const final = await waitForJob(j.id, setJob);
      if (final.status === "done") onDone(final.result);
      else {
        if (final.status === "error") setError(final.error);
        setStage(fallback);
      }
    } catch (e: any) {
      setError(e.message);
      setStage(fallback);
    } finally {
      jobRef.current = null;
    }
  }

  const writeScript = () =>
    runJob("/api/studio/script", idea, "writing", (r: Script) => { setScript(r); setStage("edit"); }, script ? "edit" : "idea");

  const render = () =>
    script && runJob("/api/studio/render", { script, ...look, motion: sys?.video_ready ? look.motion : "still" }, "rendering", (r) => { setResult(r); setStage("done"); }, "edit");

  const cancel = () => jobRef.current && post(`/api/jobs/${jobRef.current}/cancel`);

  return (
    <div className="page">
      {missing.length > 0 && (
        <div className="banner warn">
          <span>One-time setup needed: download {missing.length > 1 ? missing.slice(0, -1).join(", ") + " and " + missing[missing.length - 1] : missing[0]} (once, then everything works offline).</span>
          <button className="btn small primary" onClick={goModels}>Open setup</button>
        </div>
      )}
      {error && (
        <div className="banner error">
          <span style={{ whiteSpace: "pre-wrap" }}>{error}</span>
          <button className="icon-btn" onClick={() => setError(null)}><X size={16} /></button>
        </div>
      )}

      {stage === "idea" && <IdeaForm idea={idea} setIdea={setIdea} onSubmit={writeScript} disabled={!ready?.llm} hasDraft={!!script} onDraft={() => setStage("edit")} />}

      {(stage === "writing" || stage === "rendering") && job && (
        <ProgressCard job={job} title={stage === "writing" ? "Writing your script" : "Making your video"} onCancel={cancel} />
      )}

      {stage === "edit" && script && (
        <Editor
          script={script} setScript={setScript} look={look} setLook={setLook} voices={voices}
          onBack={() => setStage("idea")} onRewrite={writeScript} onRender={render}
          canRender={!!(ready?.image && ready?.tts && ready?.ffmpeg)} videoReady={!!sys?.video_ready} goModels={goModels}
          videoQuality={videoQuality} setVideoQuality={changeVideoQuality}
        />
      )}

      {stage === "done" && result?.project && (
        <ProjectView
          pid={result.project} goLibrary={goLibrary} onEdit={() => setStage("edit")}
          onLoaded={(p) => {
            // "Edit script & re-render" should start from this project's own settings
            setScript(p.script);
            setLook({ style: p.style, voice: p.voice, speed: p.speed, captions: p.captions, music: p.music,
                      music_volume: p.music_volume, motion: (p.motion as Motion) || "still" });
          }}
          onNew={() => { setScript(null); setResult(null); setStage("idea"); }}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ idea */
function IdeaForm({ idea, setIdea, onSubmit, disabled, hasDraft, onDraft }: {
  idea: Idea; setIdea: (f: (i: Idea) => Idea) => void; onSubmit: () => void; disabled: boolean; hasDraft: boolean; onDraft: () => void;
}) {
  const set = (patch: Partial<Idea>) => setIdea((i) => ({ ...i, ...patch }));
  return (
    <div className="idea">
      <header className="hero">
        <div className="eyebrow"><Sparkles size={14} /> Topic → Short video</div>
        <h1>What's your next video about?</h1>
        <p className="muted">Featherbox writes the script, records the voiceover, paints every scene and edits a vertical video. Everything runs on your PC, no internet needed.</p>
      </header>

      <div className="card">
        <textarea
          className="topic" rows={3} maxLength={500} autoFocus
          placeholder="e.g. Why do cats purr? Explain it like a fun science fact."
          value={idea.topic} onChange={(e) => set({ topic: e.target.value })}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && idea.topic.trim().length > 2 && !disabled) onSubmit(); }}
        />
        <div className="chips">
          {EXAMPLES.map((ex) => (
            <button key={ex} className="chip" onClick={() => set({ topic: ex })}>{ex}</button>
          ))}
        </div>

        <div className="form-grid">
          <Field label="Language">
            <Segmented value={idea.language} onChange={(v) => set({ language: v })} options={[["english", "English"], ["hindi", "हिन्दी Hindi"]]} />
          </Field>
          <Field label="Length">
            <Segmented value={String(idea.seconds)} onChange={(v) => set({ seconds: Number(v) })} options={[["30", "30 s"], ["45", "45 s"], ["60", "60 s"]]} />
          </Field>
          <Field label="Tone" wide>
            <div className="chips tight">
              {TONES.map((t) => (
                <button key={t} className={`chip cap ${idea.tone === t ? "on" : ""}`} onClick={() => set({ tone: t })}>{t}</button>
              ))}
            </div>
          </Field>
        </div>

        <div className="actions">
          {hasDraft && <button className="btn ghost" onClick={onDraft}>Back to current script</button>}
          <button className="btn primary big" disabled={disabled || idea.topic.trim().length < 3} onClick={onSubmit}>
            <Wand2 size={18} /> Write script
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ editor */
function Editor({ script, setScript, look, setLook, voices, onBack, onRewrite, onRender, canRender, videoReady, goModels,
  videoQuality, setVideoQuality }: {
  script: Script; setScript: (s: Script) => void; look: Look; setLook: (f: (l: Look) => Look) => void; voices: Voice[];
  onBack: () => void; onRewrite: () => void; onRender: () => void; canRender: boolean; videoReady: boolean; goModels: () => void;
  videoQuality: string; setVideoQuality: (q: string) => void;
}) {
  const motion: Motion = videoReady ? look.motion : "still";
  const clips = motion === "all" ? script.scenes.length : motion === "hook" ? 1 : 0;
  const [previewing, setPreviewing] = useState(false);
  const words = script.scenes.reduce((n, s) => n + s.narration.split(/\s+/).filter(Boolean).length, 0);
  const estSeconds = Math.round(words / (script.language === "hindi" ? 2.1 : 2.4) / look.speed + script.scenes.length * 0.35);
  const hindi = script.language === "hindi";
  const voiceList = voices.filter((v) => (hindi ? v.lang === "hi" : v.lang.startsWith("en")));

  const setScene = (i: number, patch: Partial<Script["scenes"][number]>) =>
    setScript({ ...script, scenes: script.scenes.map((s, k) => (k === i ? { ...s, ...patch } : s)) });

  async function preview() {
    setPreviewing(true);
    try {
      const r = await fetch("/api/tts/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: script.scenes[0]?.narration || script.title, voice: look.voice, speed: look.speed }),
      });
      if (!r.ok) throw new Error("Preview failed");
      const audio = new Audio(URL.createObjectURL(await r.blob()));
      audio.onended = () => setPreviewing(false);
      await audio.play();
    } catch {
      setPreviewing(false);
    }
  }

  return (
    <div className="editor">
      <div className="editor-main">
        <div className="row between">
          <button className="btn ghost" onClick={onBack}><ArrowLeft size={16} /> New topic</button>
          <button className="btn ghost" onClick={onRewrite}><RefreshCw size={16} /> Rewrite script</button>
        </div>
        <input className="title-input" value={script.title} onChange={(e) => setScript({ ...script, title: e.target.value })} />
        <div className="muted small">{script.scenes.length} scenes · {words} words · about {estSeconds} s</div>

        <ol className="scenes">
          {script.scenes.map((s, i) => (
            <li key={i} className="scene card">
              <div className="scene-head">
                <span className="scene-no">{i + 1}</span>
                <span className="muted small">{i === 0 ? "Hook" : i === script.scenes.length - 1 ? "Ending" : "Scene"}</span>
                <button className="icon-btn" title="Remove scene" disabled={script.scenes.length <= 2}
                  onClick={() => setScript({ ...script, scenes: script.scenes.filter((_, k) => k !== i) })}><Trash2 size={15} /></button>
              </div>
              <label className="lbl">Narration</label>
              <textarea rows={2} value={s.narration} onChange={(e) => setScene(i, { narration: e.target.value })} />
              <label className="lbl">Image</label>
              <textarea rows={2} className="mono" value={s.image_prompt} onChange={(e) => setScene(i, { image_prompt: e.target.value })} />
            </li>
          ))}
        </ol>
        <button className="btn ghost" onClick={() => setScript({ ...script, scenes: [...script.scenes, { narration: "", image_prompt: "" }] })}>
          <Plus size={16} /> Add scene
        </button>
      </div>

      <aside className="editor-side card">
        <h3>Look &amp; voice</h3>
        <label className="lbl">Visual style</label>
        <div className="styles">
          {STYLES.map((st) => (
            <button key={st.id} className={`style ${look.style === st.id ? "on" : ""}`} onClick={() => setLook((l) => ({ ...l, style: st.id }))}>
              <span className="swatch" style={{ background: st.swatch }} />
              {st.label}
            </button>
          ))}
        </div>

        <label className="lbl">Voice</label>
        <div className="row">
          <select value={look.voice} onChange={(e) => setLook((l) => ({ ...l, voice: e.target.value }))}>
            {voiceList.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
          </select>
          <button className="icon-btn boxed" title="Preview voice" onClick={preview} disabled={previewing || !voiceList.length}>
            {previewing ? <Loader2 size={16} className="spin" /> : <Volume2 size={16} />}
          </button>
        </div>

        <label className="lbl">Speaking speed · {look.speed.toFixed(2)}×</label>
        <input type="range" min={0.8} max={1.3} step={0.05} value={look.speed} onChange={(e) => setLook((l) => ({ ...l, speed: Number(e.target.value) }))} />

        <label className="lbl">Background music</label>
        <MusicPicker value={look.music} volume={look.music_volume} tone={script.tone}
          onChange={(music, music_volume) => setLook((l) => ({ ...l, music, music_volume }))} />

        <label className="lbl">Motion</label>
        <select value={motion} disabled={!videoReady} onChange={(e) => setLook((l) => ({ ...l, motion: e.target.value as Motion }))}>
          <option value="still">Still pictures (slow zoom &amp; pan)</option>
          <option value="hook">AI motion on the first scene</option>
          <option value="all">AI motion on every scene</option>
        </select>
        {videoReady ? (
          <>
            {clips > 0 && (
              <div style={{ marginTop: 8 }}>
                <Segmented value={videoQuality} onChange={setVideoQuality}
                  options={[["fast", "Fast clips"], ["best", "Best quality"]]} />
              </div>
            )}
            <p className="muted tiny">{clips ? `${clips} AI clip${clips > 1 ? "s" : ""}: each one ${MOTION_TIME[videoQuality] ?? MOTION_TIME.fast}. You can animate single scenes later too.` : "The video model brings pictures to life. You can also animate single scenes after rendering."}</p>
          </>
        ) : (
          <p className="muted tiny">AI motion needs a video model. <button className="link" onClick={goModels}>Get one in Models → Video</button></p>
        )}

        <label className="toggle">
          <input type="checkbox" checked={look.captions} onChange={(e) => setLook((l) => ({ ...l, captions: e.target.checked }))} />
          <span>Burn-in captions</span>
        </label>

        <button className="btn primary big full" disabled={!canRender} onClick={onRender}>
          <Play size={18} /> Make video
        </button>
        <p className="muted small">On a 4 GB GPU expect about 40 s per scene picture{clips ? ", plus the AI clips" : ""}.</p>
      </aside>
    </div>
  );
}

/* ------------------------------------------------------------------ progress */
function ProgressCard({ job, title, onCancel }: { job: Job; title: string; onCancel: () => void }) {
  const [start] = useState(() => Date.now());
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const elapsed = Math.round((now - start) / 1000);
  const pct = Math.round(job.progress * 100);
  return (
    <div className="progress-card card">
      <div className="pulse"><Loader2 size={28} className="spin" /></div>
      <h2>{title}</h2>
      <p className="muted">{job.status === "queued" ? "Waiting for the GPU…" : job.stage}</p>
      <div className="bar big"><div style={{ width: `${pct}%` }} /></div>
      <div className="row between muted small">
        <span>{pct}%</span>
        <span>{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")} elapsed</span>
      </div>
      <button className="btn ghost" onClick={onCancel}><X size={16} /> Cancel</button>
    </div>
  );
}

/* ------------------------------------------------------------------ bits */
function Field({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`field ${wide ? "wide" : ""}`}>
      <div className="lbl">{label}</div>
      {children}
    </div>
  );
}

function Segmented({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <div className="segmented">
      {options.map(([v, l]) => (
        <button key={v} className={value === v ? "on" : ""} onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  );
}
