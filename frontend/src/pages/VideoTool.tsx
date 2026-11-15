import { useEffect, useRef, useState } from "react";
import { Clapperboard, Download, FolderOpen, Image as ImageIcon, Loader2, RotateCcw, Trash2, Upload, Wand2, X } from "lucide-react";
import { api, post, save, store, type Creation, type ToolOptions } from "../api";
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
  const { job, running, run, cancel } = useJob();

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

  if (opts && !models.length) {
    return (
      <div className="page">
        <header className="page-head"><h1>Video</h1></header>
        <div className="card empty">
          <Clapperboard size={28} />
          <h3>No video model yet</h3>
          <p className="muted">Download one in Models → Video. Wan 2.2 5B can bring your pictures to life, even on a 4 GB card.</p>
          <button className="btn primary" onClick={goModels}>Open Models</button>
        </div>
      </div>
    );
  }

  return (
    <div className="page wide">
      <header className="page-head">
        <h1>Video</h1>
        <p className="muted">Make a short AI clip from a description, or bring one of your pictures to life.</p>
      </header>
      {error && <div className="banner error"><span>{error}</span><button className="icon-btn" onClick={() => setError(null)}><X size={16} /></button></div>}

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
          <select value={model} onChange={(e) => set({ model: e.target.value })}>
            {models.map((m) => <option key={m.id} value={m.id}>{m.name}{m.i2v ? "" : " (text only)"}</option>)}
          </select>

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

          {running ? <JobProgress job={job} onCancel={cancel} /> : (
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
