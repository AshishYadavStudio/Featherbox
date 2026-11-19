import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Clapperboard, Download, FolderOpen, Image as ImageIcon, Loader2, Lock, RotateCcw, Trash2, Upload, Wand2, X } from "lucide-react";
import { api, fmtMB, fmtSize, post, save, store, type Catalog, type Component, type Creation, type ToolOptions } from "../api";
import { JobProgress, Segmented, ago, fmtSecs, useJob } from "../components/ToolKit";

type Start = { ref: string; url: string } | null;
type Form = { prompt: string; model: string; aspect: string; seed: string };
const EMPTY: Form = { prompt: "", model: "", aspect: "portrait", seed: "" };
const TIME: Record<string, string> = { fast: "about 2–3 min", best: "about 6–7 min" };

/** Prompt (and optionally a picture) → short AI video clip. */
export default function VideoTool({ goModels }: { goModels: () => void }) {
  const [opts, setOpts] = useState<ToolOptions | null>(null);
  const [form, setForm] = useState<Form>(() => ({ ...EMPTY, ...store<Partial<Form>>("featherbox.videoForm", {}) }));
  const [start, setStart] = useState<Start>(null);
  const [images, setImages] = useState<Creation[] | null>(null);   // picker: null = closed
  const [items, setItems] = useState<Creation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [quality, setQuality] = useState("fast");
  const file = useRef<HTMLInputElement>(null);
  const { job, running, run, cancel } = useJob("video", (f) => { if (f.status === "error") setError(f.error); load(); });

  const load = () => api<Creation[]>("/api/creations/video").then(setItems).catch(() => {});
  useEffect(() => {
    api<ToolOptions>("/api/tools/options").then((o) => { setOpts(o); setQuality(o.video_quality); }).catch((e) => setError(e.message));
    load();
    // "Animate this picture" from the Image page
    const pending = store<string | null>("featherbox.videoStart", null);
    if (pending) {
      const cid = pending.split(":")[1];
      setStart({ ref: pending, url: `/creations/image/${cid}.png` });
      save("featherbox.videoStart", null);
      // keep the picture's shape (a landscape picture shouldn't be cropped to portrait)
      api<Creation[]>("/api/creations/image").then((list) => {
        const pic = list.find((c) => c.id === cid);
        if (pic?.aspect) setForm((f) => ({ ...f, aspect: pic.aspect! }));
      }).catch(() => {});
    }
  }, []);
  useEffect(() => save("featherbox.videoForm", form), [form]);

  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }));
  const models = opts?.video_models ?? [];
  const model = form.model && models.some((m) => m.id === form.model) ? form.model : opts?.active.video || "";
  const canUsePicture = !!models.find((m) => m.id === model)?.i2v;
  const blocked = !!start && !canUsePicture;

  async function upload(f: File) {
    setUploading(true); setError(null);
    try {
      const r = await fetch("/api/creations/upload", { method: "POST", body: f });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || "Upload failed");
      setStart({ ref: body.ref, url: body.url });
      set({ aspect: body.width > body.height * 1.15 ? "landscape" : body.height > body.width * 1.15 ? "portrait" : "square" });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploading(false);
      if (file.current) file.current.value = "";
    }
  }

  function changeQuality(q: string) {
    setQuality(q);
    api("/api/settings", { method: "PATCH", body: JSON.stringify({ video_quality: q }) }).catch(() => {});
  }

  async function generate() {
    setError(null);
    try {
      await run("/api/tools/video", { prompt: form.prompt, image: start?.ref ?? null, model, aspect: form.aspect,
        seed: form.seed.trim() ? Number(form.seed) : -1 });
    } catch (e: any) {
      if (e.message !== "Cancelled") setError(e.message);
    }
    load();
  }

  async function remove(c: Creation) {
    if (!confirm("Delete this clip?")) return;
    await api(`/api/creations/video/${c.id}`, { method: "DELETE" });
    load();
  }

  const reuse = (c: Creation) => {
    set({ prompt: c.prompt || "", model: c.model || "", aspect: c.aspect || "portrait", seed: String(c.seed ?? "") });
    if (c.image) setStart({ ref: c.image, url: c.image.startsWith("upload:") ? `/creations/uploads/${c.image.slice(7)}.png` : `/creations/image/${c.image.slice(6)}.png` });
  };

  // No video model yet: show the whole page anyway (so people can see what it does and type a prompt),
  // with a card to download a model right here. Creating a clip stays locked until one is installed.
  const noModel = !!opts && !models.length;
  const reloadOpts = () => api<ToolOptions>("/api/tools/options")
    .then((o) => { setOpts(o); setQuality(o.video_quality); }).catch((e) => setError(e.message));

  return (
    <div className="page wide">
      <header className="page-head">
        <h1>Video</h1>
        <p className="muted">Make a short AI clip from a description, or bring one of your pictures to life.</p>
      </header>
      {error && <div className="banner error"><span>{error}</span><button className="icon-btn" onClick={() => setError(null)}><X size={16} /></button></div>}
      {noModel && <VideoModelSetup onReady={reloadOpts} goModels={goModels} />}

      <div className="tool">
        <aside className="card tool-form">
          <label className="lbl">Start from a picture (optional)</label>
          {start ? (
            <div className="start-pic">
              <img src={start.url} alt="Start picture" />
              <div>
                <p className="small">The clip begins with this picture and brings it to life.</p>
                <button className="btn small ghost" onClick={() => setStart(null)}><X size={14} /> Remove</button>
              </div>
            </div>
          ) : (
            <div className="row wrap">
              <button className="btn small" disabled={uploading} onClick={() => file.current?.click()}>
                {uploading ? <Loader2 size={14} className="spin" /> : <Upload size={14} />} Upload picture
              </button>
              <button className="btn small" onClick={() => api<Creation[]>("/api/creations/image").then(setImages)}>
                <ImageIcon size={14} /> Pick from your images
              </button>
              <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" hidden
                onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            </div>
          )}
          {blocked && <p className="tiny" style={{ color: "var(--warn)" }}>This model makes clips from text only. Pick a model that animates pictures, or remove the picture.</p>}

          <label className="lbl">{start ? "What should move?" : "Describe the clip"}</label>
          <textarea rows={4} value={form.prompt}
            placeholder={start ? "e.g. waves roll in, mist drifts, slow camera push" : "e.g. Waves rolling onto a beach at sunset, slow camera move"}
            onChange={(e) => set({ prompt: e.target.value })} />

          <label className="lbl">Model</label>
          {noModel ? (
            <select disabled><option>No video model yet (download one above)</option></select>
          ) : (
            <select value={model} onChange={(e) => set({ model: e.target.value })}>
              {models.map((m) => <option key={m.id} value={m.id}>{m.name}{m.i2v ? "" : " (text only)"}</option>)}
            </select>
          )}

          <label className="lbl">Shape</label>
          <Segmented value={form.aspect} onChange={(aspect) => set({ aspect })}
            options={[["portrait", "Portrait 9:16"], ["square", "Square"], ["landscape", "Landscape 16:9"]]} />
          {start && <p className="muted tiny">The picture is cropped to this shape.</p>}

          <label className="lbl">Quality</label>
          <Segmented value={quality} onChange={changeQuality} options={[["fast", "Fast"], ["best", "Best quality"]]} />
          <p className="muted tiny">About 2 s of video, {TIME[quality] ?? TIME.fast} on a 4 GB GPU (faster on bigger cards).</p>

          <details className="adv">
            <summary className="lbl">Advanced</summary>
            <label className="lbl">Seed (same seed + prompt = same clip)</label>
            <input value={form.seed} placeholder="Random" inputMode="numeric" onChange={(e) => set({ seed: e.target.value.replace(/[^0-9]/g, "") })} />
          </details>

          {noModel ? (
            <button className="btn primary big full" disabled title="Download a video model first">
              <Lock size={18} /> Get a video model to create clips
            </button>
          ) : running ? <JobProgress job={job} onCancel={cancel} /> : (
            <button className="btn primary big full" disabled={!form.prompt.trim() || !model || blocked} onClick={generate}>
              <Wand2 size={18} /> {start ? "Animate picture" : "Create clip"}
            </button>
          )}
        </aside>

        <section className="gallery-wrap">
          <div className="row between">
            <h3>Your clips <span className="muted small">· {items.length}</span></h3>
            {items.length > 0 && <button className="btn ghost small" onClick={() => post("/api/creations/video/reveal")}><FolderOpen size={14} /> Open folder</button>}
          </div>
          {!items.length && <p className="muted small">Clips you create appear here, saved on your PC.</p>}
          <div className="gallery">
            {items.map((c) => (
              <figure key={c.id} className="creation card">
                <div className="creation-media">
                  <video src={c.url} poster={c.thumb} controls loop playsInline preload="none" style={{ aspectRatio: `${c.width} / ${c.height}` }} />
                </div>
                <figcaption>
                  <p className="clamp2 small">{c.prompt}</p>
                  <div className="muted tiny">{c.model_name} · {c.image ? "from a picture · " : ""}{fmtSecs(c.seconds)} · {ago(c.created)}</div>
                  <div className="row wrap">
                    <a className="icon-btn" href={c.url} download={`featherbox-${c.id}.mp4`} title="Download"><Download size={15} /></a>
                    <button className="icon-btn" title="Use these settings again" onClick={() => reuse(c)}><RotateCcw size={15} /></button>
                    <button className="icon-btn" title="Delete" onClick={() => remove(c)}><Trash2 size={15} /></button>
                  </div>
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      </div>

      {images && (
        <div className="lightbox" onClick={() => setImages(null)}>
          <div className="card picker" onClick={(e) => e.stopPropagation()}>
            <div className="row between">
              <h3>Pick a picture</h3>
              <button className="icon-btn" onClick={() => setImages(null)}><X size={16} /></button>
            </div>
            {!images.length && <p className="muted small">No images yet. Make some on the Image page, or upload a picture.</p>}
            <div className="picker-grid">
              {images.map((c) => (
                <button key={c.id} onClick={() => {
                  setStart({ ref: `image:${c.id}`, url: c.url });
                  set({ aspect: c.aspect || form.aspect });
                  setImages(null);
                }}>
                  <img src={c.url} alt={c.prompt} loading="lazy" />
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ no video model yet */
const BUSY = ["queued", "downloading", "extracting"];
const FIT: Record<string, [string, string]> = {
  great: ["Runs well", "ok"], recommended: ["Runs well", "ok"], slow: ["Runs slowly", "warn"],
  "very-slow": ["Slow on this PC", "warn"], "too-big": ["Probably won't run", "bad"], "other-backend": ["For other hardware", "dim"],
};

/** Pick and download a video model without leaving the page. Also fetches the stable-diffusion.cpp
 *  engine if it is missing (video runs on the same engine as pictures). Calls onReady once a model works. */
function VideoModelSetup({ onReady, goModels }: { onReady: () => void; goModels: () => void }) {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api<Catalog>("/api/catalog").then(setCat).catch((e) => setErr(e.message)), []);

  const all = cat?.components ?? [];
  // image-to-video first: it animates your own pictures, so it is the one to recommend
  const videos = all.filter((c) => c.kind === "model" && c.category === "video" && !c.soon)
    .sort((a, b) => Number(!!b.i2v) - Number(!!a.i2v));
  const engines = all.filter((c) => c.kind === "engine" && c.id.startsWith("sd-"));
  const engineOk = engines.some((c) => c.installed);
  const engine = engineOk ? null : engines.find((c) => cat?.recommended.includes(c.id)) ?? null;
  const busy = [...videos, ...engines].some((c) => c.download && BUSY.includes(c.download.status));

  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(load, busy ? 1000 : 5000); return () => clearInterval(t); }, [load, busy]);

  // Done: make the model that was just downloaded the one in use, then unlock the page.
  const ready = engineOk ? videos.find((c) => c.installed) : undefined;
  const fired = useRef(false);
  useEffect(() => {
    if (!ready || fired.current) return;
    fired.current = true;
    const fresh = videos.find((c) => c.installed && c.download?.status === "done") ?? ready;
    api("/api/settings", { method: "PATCH", body: JSON.stringify({ video_model: fresh.id }) }).catch(() => {}).finally(onReady);
  }, [ready?.id]);

  async function install(c: Component) {
    if (c.fit === "too-big" && !confirm(`${c.name} probably won't run on this PC.\n${c.reason}\n\nDownload it anyway?`)) return;
    setErr(null);
    try {
      if (engine) await post(`/api/catalog/${engine.id}/install`);
      await post(`/api/catalog/${c.id}/install`);
    } catch (e: any) { setErr(e.message); }
    load();
  }
  async function pause(c: Component) {
    await post(`/api/catalog/${c.id}/cancel`).catch(() => {});
    if (engine?.download && BUSY.includes(engine.download.status)) await post(`/api/catalog/${engine.id}/cancel`).catch(() => {});
    load();
  }

  return (
    <section className="card video-setup">
      <div className="row between wrap">
        <div>
          <h3><Clapperboard size={18} /> Get a video model to start</h3>
          <p className="muted small">
            This page needs one AI video model: a one-time download, then it works offline. Have a look around below;
            your prompt and picture are kept while it downloads.
          </p>
        </div>
        <button className="btn ghost small" onClick={goModels}>All models</button>
      </div>
      {err && <div className="err tiny">{err}</div>}
      {!cat && <p className="muted small"><Loader2 size={14} className="spin" /> Checking what fits this PC…</p>}
      <div className="video-setup-list">
        {videos.map((c, i) => {
          const d = c.download;
          const mine = !!d && d.status !== "done";                 // this model has a download going, paused or failed
          const rowBusy = !!d && BUSY.includes(d.status);
          const parts = [c, ...(engine && mine ? [engine] : [])].filter((x) => x.download);
          const total = parts.reduce((n, x) => n + (x.download!.total || x.download_mb * 1048576), 0);
          const done = parts.reduce((n, x) => n + (x.installed || x.download!.status === "done"
            ? x.download!.total : Math.min(x.download!.done, x.download!.total)), 0);
          const speed = parts.reduce((n, x) => n + (x.download!.status === "downloading" ? x.download!.speed : 0), 0);
          const pct = total ? Math.min(100, (done / total) * 100) : 0;
          const unpacking = parts.some((x) => x.download!.status === "extracting");
          const [fitText, fitTone] = FIT[c.fit] ?? [c.fit, "dim"];
          const sizeMB = c.download_mb + (engine ? engine.download_mb : 0);
          let state = `${Math.floor(pct)}% · ${fmtMB(done)} of ${fmtMB(total)}${speed ? ` · ${fmtMB(speed)}/s` : ""}`;
          if (d?.status === "cancelled") state = `Paused at ${fmtMB(done)} of ${fmtMB(total)}`;
          else if (unpacking) state = "Unpacking…";
          else if (d?.status === "queued") state = "Starting…";
          return (
            <div key={c.id} className="video-setup-item">
              <div className="model-info">
                <div className="row wrap">
                  <strong>{c.name}</strong>
                  {i === 0 && c.i2v && <span className="badge accent">Recommended</span>}
                  <span className="badge">{c.i2v ? "Animates your pictures" : "Text only"}</span>
                  <span className={`badge ${fitTone}`}>{fitText}</span>
                </div>
                <div className="muted small">{c.description}</div>
                {c.fit !== "great" && c.fit !== "recommended" && <div className="small">{c.reason}</div>}
                <div className="muted tiny">
                  {fmtSize(sizeMB)} to download{engine ? ` (includes the ${fmtSize(engine.download_mb)} picture and video engine)` : ""} · {c.license}
                </div>
                {mine && (
                  <div className="dl">
                    <div className="bar"><div style={{ width: `${pct}%` }} /></div>
                    <div className="muted tiny">
                      {d!.status === "error" ? <span className="err">{d!.error || "Download failed"}</span> : state}
                    </div>
                  </div>
                )}
              </div>
              <div className="model-act">
                {c.installed && !engineOk ? <span className="badge"><Loader2 size={12} className="spin" /> Waiting for the engine</span>
                  : c.installed ? <span className="badge ok"><Check size={12} /> Installed</span>
                  : rowBusy ? <button className="btn small" onClick={() => pause(c)}><X size={14} /> Pause</button>
                  : (
                    <button className={`btn small ${i === 0 ? "primary" : ""}`} disabled={busy} onClick={() => install(c)}>
                      <Download size={14} /> {d?.status === "cancelled" || d?.status === "error" ? "Resume" : "Download"}
                    </button>
                  )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
