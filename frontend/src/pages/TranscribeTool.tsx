import { useEffect, useRef, useState } from "react";
import { Check, Copy, Download, FileAudio, FolderOpen, Loader2, Mic, ScrollText, Square, Trash2, Upload, Wand2, X } from "lucide-react";
import { api, post, save, store, type Catalog, type Creation, type TranscribeOptions } from "../api";
import { JobProgress, ago, fmtSecs, useJob } from "../components/ToolKit";

type Form = { language: string; model: string; translate: boolean; prompt: string };
const EMPTY: Form = { language: "auto", model: "", translate: false, prompt: "" };
type Source = { file: Blob; name: string; recorded: boolean; url: string; seconds: number | null };

const clock = (t: number) => {
  const s = Math.max(0, Math.floor(t));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return `${h ? `${h}:${String(m).padStart(2, "0")}` : m}:${String(sec).padStart(2, "0")}`;
};

/** Upload with progress (big videos take a moment even on localhost). */
function uploadMedia(file: Blob, name: string, onProgress: (p: number) => void): Promise<{ ref: string }> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", `/api/creations/media?name=${encodeURIComponent(name)}`);
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => {
      let body: any = {};
      try { body = JSON.parse(x.responseText); } catch { /* not json */ }
      x.status < 300 ? resolve(body) : reject(new Error(body.detail || "Upload failed"));
    };
    x.onerror = () => reject(new Error("Upload failed"));
    x.send(file);
  });
}

/** Recording or video → written text with timestamps, on the CPU. */
export default function TranscribeTool({ goModels }: { goModels: () => void }) {
  const [opts, setOpts] = useState<TranscribeOptions | null>(null);
  const [form, setForm] = useState<Form>(() => ({ ...EMPTY, ...store<Partial<Form>>("featherbox.transcribeForm", {}) }));
  const [src, setSrc] = useState<Source | null>(null);
  const [items, setItems] = useState<Creation[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<number | null>(null);
  const [rec, setRec] = useState<{ r: MediaRecorder; t0: number } | null>(null);
  const [now, setNow] = useState(Date.now());
  const [setup, setSetup] = useState<Catalog | null>(null);
  const [drag, setDrag] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const { job, running, run, cancel } = useJob();

  const load = () => api<Creation[]>("/api/creations/transcript").then((l) => { setItems(l); return l; }).catch(() => [] as Creation[]);
  const loadOpts = () => api<TranscribeOptions>("/api/tools/transcribe/options").then(setOpts).catch((e) => setError(e.message));
  useEffect(() => { loadOpts(); load().then((l) => l[0] && setOpenId(l[0].id)); }, []);
  useEffect(() => save("featherbox.transcribeForm", form), [form]);
  useEffect(() => { if (!rec) return; const t = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(t); }, [rec]);
  useEffect(() => () => { if (src) URL.revokeObjectURL(src.url); }, [src]);

  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }));
  const models = opts?.models ?? [];
  const model = form.model && models.some((m) => m.id === form.model) ? form.model : opts?.active || "";
  const m = models.find((x) => x.id === model);
  const canTranslate = !!m?.translates;
  const ready = !!opts && opts.engine && models.length > 0;

  // ---------------------------------------------------------------- one-click install
  const installing = setup?.components.filter((c) => opts?.setup.includes(c.id)) ?? [];
  useEffect(() => {
    if (!setup) return;
    const busy = installing.some((c) => c.download && ["queued", "downloading", "extracting"].includes(c.download.status));
    if (!busy) { loadOpts(); if (installing.every((c) => c.installed)) setSetup(null); return; }
    const t = setTimeout(() => api<Catalog>("/api/catalog").then(setSetup), 1000);
    return () => clearTimeout(t);
  }, [setup]);
  async function install() {
    setError(null);
    try {
      for (const id of opts?.setup ?? []) await post(`/api/catalog/${id}/install`);
      setSetup(await api<Catalog>("/api/catalog"));
    } catch (e: any) { setError(e.message); }
  }

  // ---------------------------------------------------------------- input
  function pick(f: Blob, name: string, recorded: boolean) {
    const url = URL.createObjectURL(f);
    setSrc({ file: f, name, recorded, url, seconds: null });
    const probe = document.createElement(f.type.startsWith("video") ? "video" : "audio");
    probe.preload = "metadata";
    probe.onloadedmetadata = () => isFinite(probe.duration) && setSrc((s) => (s && s.url === url ? { ...s, seconds: probe.duration } : s));
    probe.src = url;
  }

  async function startRec() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const r = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      r.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      r.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: r.mimeType || "audio/webm" });
        const stamp = new Date().toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
        pick(blob, `Recording ${stamp}`, true);
        setRec(null);
      };
      r.start(1000);
      setRec({ r, t0: Date.now() });
    } catch {
      setError("Featherbox couldn't use the microphone. Check that one is connected and allowed in Windows Settings → Privacy → Microphone.");
    }
  }

  // ---------------------------------------------------------------- transcribe
  async function transcribe() {
    if (!src) return;
    setError(null);
    try {
      setUploading(0);
      const { ref } = await uploadMedia(src.file, src.name, setUploading);
      setUploading(null);
      const final = await run("/api/tools/transcribe", {
        media: ref, title: src.name.replace(/\.[a-z0-9]{2,4}$/i, ""), model, language: form.language,
        translate: canTranslate && form.translate, prompt: form.prompt,
      });
      const made = final.result?.items?.[0];
      if (made) { setOpenId(made.id); setSrc(null); }
    } catch (e: any) {
      if (e.message !== "Cancelled") setError(e.message);
    } finally {
      setUploading(null);
    }
    load();
  }

  async function remove(c: Creation) {
    if (!confirm("Delete this transcript?")) return;
    await api(`/api/creations/transcript/${c.id}`, { method: "DELETE" });
    load();
  }

  const auto = form.language === "auto" ? 1 : 0;   // detecting the language costs one extra pass
  const estimate = src?.seconds && m?.rtf ? Math.max(4, src.seconds * m.rtf + 3 + auto * Math.min(30, src.seconds) * m.rtf) : null;
  const [gettingGpu, setGettingGpu] = useState(false);
  async function getGpuEngine() {
    setGettingGpu(true); setError(null);
    try {
      await post("/api/catalog/whisper-cuda/install");
      for (;;) {
        await new Promise((res) => setTimeout(res, 1500));
        const cat = await api<Catalog>("/api/catalog");
        const c = cat.components.find((x) => x.id === "whisper-cuda");
        if (c?.installed) break;
        if (c?.download?.status === "error" || c?.download?.status === "cancelled") throw new Error(c.download.error || "Download stopped");
      }
      await loadOpts();
    } catch (e: any) { setError(e.message); } finally { setGettingGpu(false); }
  }

  if (opts && !ready) {
    const busy = installing.find((c) => c.download && ["queued", "downloading", "extracting"].includes(c.download.status));
    const d = busy?.download;
    return (
      <div className="page">
        <header className="page-head"><h1>Transcribe</h1></header>
        <div className="card empty">
          <ScrollText size={28} />
          <h3>Speech to text isn't set up yet</h3>
          <p className="muted">One download gets the whisper.cpp engine and Whisper Large v3 Turbo (about 560 MB). It runs on
            your processor, understands 99 languages including Hindi, and works offline.</p>
          {error && <p className="err small">{error}</p>}
          {d ? (
            <div className="dl" style={{ width: "100%", maxWidth: 360 }}>
              <div className="bar"><div style={{ width: `${d.total ? (100 * d.done) / d.total : 0}%` }} /></div>
              <div className="muted tiny">{d.status === "extracting" ? "Unpacking…" : `Downloading ${busy!.name}…`}</div>
            </div>
          ) : (
            <div className="row wrap" style={{ justifyContent: "center" }}>
              <button className="btn primary" onClick={install}><Download size={16} /> Set up speech to text</button>
              <button className="btn ghost" onClick={goModels}>Choose a model in Models</button>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="page wide">
      <header className="page-head">
        <h1>Transcribe</h1>
        <p className="muted">Turn a recording, a voice note or a video into text and subtitles. Runs on this PC; nothing is uploaded anywhere.</p>
      </header>
      {error && <div className="banner error"><span>{error}</span><button className="icon-btn" onClick={() => setError(null)}><X size={16} /></button></div>}
      {opts?.gpu_engine_available && (
        <div className="banner"><span>Your NVIDIA graphics card can transcribe about 10× faster. The GPU engine is a 258 MB download.</span>
          <button className="btn small" disabled={gettingGpu} onClick={getGpuEngine}>
            {gettingGpu ? <Loader2 size={14} className="spin" /> : <Download size={14} />} {gettingGpu ? "Downloading…" : "Get GPU engine"}
          </button></div>
      )}
      {opts && !opts.ffmpeg && <div className="banner warn"><span>Transcribe needs FFmpeg to read recordings. Install it in Models → Engines.</span></div>}

      <div className="tool">
        <aside className="card tool-form">
          <label className="lbl">Recording or video</label>
          {rec ? (
            <div className="dropzone recording">
              <span className="rec-dot" /> Recording… {clock((now - rec.t0) / 1000)}
              <button className="btn small" onClick={() => rec.r.stop()}><Square size={14} /> Stop</button>
            </div>
          ) : src ? (
            <div className="source">
              <div className="row between">
                <span className="small"><FileAudio size={14} /> {src.name}</span>
                <button className="icon-btn" title="Remove" disabled={running || uploading !== null} onClick={() => setSrc(null)}><X size={15} /></button>
              </div>
              <span className="muted tiny">{src.seconds ? clock(src.seconds) : ""}{src.seconds ? " · " : ""}{(src.file.size / 1024 ** 2).toFixed(1)} MB</span>
              {src.recorded && <audio src={src.url} controls />}
            </div>
          ) : (
            <div className={`dropzone ${drag ? "over" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) pick(f, f.name, false); }}>
              <p className="small muted">Drop an audio or video file here</p>
              <div className="row wrap" style={{ justifyContent: "center" }}>
                <button className="btn small" onClick={() => file.current?.click()}><Upload size={14} /> Choose file</button>
                <button className="btn small" onClick={startRec}><Mic size={14} /> Record</button>
              </div>
              <input ref={file} type="file" hidden accept="audio/*,video/*,.mp3,.wav,.m4a,.ogg,.opus,.flac,.aac,.wma,.mp4,.mkv,.mov,.webm,.avi"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) pick(f, f.name, false); e.target.value = ""; }} />
            </div>
          )}

          <label className="lbl">Spoken language</label>
          <select value={form.language} onChange={(e) => set({ language: e.target.value })}>
            {Object.entries(opts?.languages ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          {form.language === "auto" && <p className="muted tiny">Picking the language is quicker and more reliable for short clips and Hindi-English mixes.</p>}

          <label className="lbl">Model</label>
          <select value={model} onChange={(e) => set({ model: e.target.value })}>
            {models.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>

          <label className="toggle small">
            <input type="checkbox" disabled={!canTranslate} checked={canTranslate && form.translate} onChange={(e) => set({ translate: e.target.checked })} />
            Translate to English
          </label>
          {!canTranslate && <p className="muted tiny">Turbo writes down what was said in its own language. For English translation, add Whisper Small or Large v3 in Models.</p>}

          <details className="adv">
            <summary className="lbl">Advanced</summary>
            <label className="lbl">Names and words to expect</label>
            <textarea rows={2} value={form.prompt} placeholder="e.g. Featherbox, Ashish, Lucknow, GST"
              onChange={(e) => set({ prompt: e.target.value })} />
            <p className="muted tiny">Helps with spelling of names and jargon.</p>
          </details>

          {uploading !== null ? (
            <div className="tool-progress">
              <span className="small"><Loader2 size={14} className="spin" /> Reading the file… {Math.round(uploading * 100)}%</span>
              <div className="bar"><div style={{ width: `${uploading * 100}%` }} /></div>
            </div>
          ) : running ? <JobProgress job={job} onCancel={cancel} queued={opts?.gpu ? "Waiting for the GPU (another job is running)…" : "Waiting for another transcription to finish…"} /> : (
            <button className="btn primary big full" disabled={!src || !model || !!rec || (opts ? !opts.ffmpeg : true)} onClick={transcribe}>
              <Wand2 size={18} /> {canTranslate && form.translate ? "Transcribe & translate" : "Transcribe"}
            </button>
          )}
          <p className="muted tiny">{estimate ? `About ${fmtSecs(estimate)} for this file on your PC.` : "Usually much faster than the recording's length."}{opts?.gpu ? " Runs on your graphics card." : ""}</p>
        </aside>

        <section className="gallery-wrap">
          <div className="row between">
            <h3>Your transcripts <span className="muted small">· {items.length}</span></h3>
            {items.length > 0 && <button className="btn ghost small" onClick={() => post("/api/creations/transcript/reveal")}><FolderOpen size={14} /> Open folder</button>}
          </div>
          {!items.length && <p className="muted small">Transcripts appear here with timestamps, ready to copy or download as subtitles (SRT/VTT).</p>}
          <div className="voice-list">
            {items.map((c) => c.id === openId
              ? <TranscriptView key={c.id} c={c} onClose={() => setOpenId(null)} onDelete={() => remove(c)} />
              : (
                <button key={c.id} className="card voice-item transcript-row" onClick={() => setOpenId(c.id)}>
                  <strong className="small">{c.title}</strong>
                  <p className="clamp2 small muted">{c.text}</p>
                  <span className="muted tiny">{meta(c)}</span>
                </button>
              ))}
          </div>
        </section>
      </div>
    </div>
  );
}

const meta = (c: Creation) => [c.language_name || c.spoken, c.translate ? "→ English" : "", clock(c.duration || 0),
  `${c.words} words`, c.model_name, `took ${fmtSecs(c.seconds)}`, ago(c.created)].filter(Boolean).join(" · ");

function TranscriptView({ c, onClose, onDelete }: { c: Creation; onClose: () => void; onDelete: () => void }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [t, setT] = useState(-1);
  const [copied, setCopied] = useState(false);
  const segs = c.segments ?? [];
  const copy = async () => { await navigator.clipboard.writeText(c.text || ""); setCopied(true); setTimeout(() => setCopied(false), 1500); };
  const seek = (s: number) => { if (audio.current) { audio.current.currentTime = s; audio.current.play(); } };
  return (
    <div className="card voice-item fresh transcript">
      <div className="row between">
        <strong>{c.title}</strong>
        <button className="icon-btn" title="Close" onClick={onClose}><X size={15} /></button>
      </div>
      <span className="muted tiny">{meta(c)}</span>
      {c.audio && <audio ref={audio} src={c.audio} controls preload="metadata" onTimeUpdate={(e) => setT(e.currentTarget.currentTime)} />}
      <div className="segments" lang={c.translate ? "en" : c.spoken}>
        {segs.map((s, i) => (
          <button key={i} className={`seg ${t >= s.start && t < s.end ? "on" : ""}`} onClick={() => seek(s.start)} title="Play from here">
            <time>{clock(s.start)}</time><span>{s.text}</span>
          </button>
        ))}
      </div>
      <div className="row wrap">
        <button className="btn small" onClick={copy}>{copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy text"}</button>
        <a className="btn small" href={c.url} download={`${c.title}.txt`}><Download size={14} /> Text</a>
        {c.srt && <a className="btn small" href={c.srt} download={`${c.title}.srt`}><Download size={14} /> Subtitles (SRT)</a>}
        {c.vtt && <a className="btn small ghost" href={c.vtt} download={`${c.title}.vtt`}>VTT</a>}
        <button className="icon-btn" title="Delete" onClick={onDelete}><Trash2 size={15} /></button>
      </div>
    </div>
  );
}
