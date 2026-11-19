import { useEffect, useState } from "react";
import { Clapperboard, Download, FolderOpen, ImagePlus, RotateCcw, Trash2, Wand2, X } from "lucide-react";
import { api, fmtAbout, post, save, store, type Creation, type Estimates, type ToolOptions } from "../api";
import { JobProgress, PageLoading, Segmented, ago, ctrlEnter, fmtSecs, useJob } from "../components/ToolKit";
import { STYLES } from "./Studio";

type Form = { prompt: string; negative: string; model: string; aspect: string; style: string; count: number; seed: string };
const EMPTY: Form = { prompt: "", negative: "", model: "", aspect: "portrait", style: "none", count: 1, seed: "" };
const IDEAS = [
  "A cozy tea stall on a rainy Mumbai street at dusk, warm lights, reflections on the wet road",
  "A red panda astronaut floating inside a space station, soft light, detailed",
  "Minimal flat illustration of a lighthouse at sunrise, pastel colors",
];

/** Prompt → picture, with any installed image model. */
export default function ImageTool({ goModels, onAnimate, canAnimate }: {
  goModels: () => void; onAnimate: (ref: string) => void; canAnimate: boolean;
}) {
  const [opts, setOpts] = useState<ToolOptions | null>(null);
  const [form, setForm] = useState<Form>(() => ({ ...EMPTY, ...store<Partial<Form>>("featherbox.imageForm", {}) }));
  const [items, setItems] = useState<Creation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Creation | null>(null);
  const [est, setEst] = useState<Estimates | null>(null);   // this PC's timings (refreshed after each run)
  // a job started before you left this page is picked up again; its pictures appear when it finishes
  const { job, running, run, cancel } = useJob("image", (f) => { if (f.status === "error") setError(f.error); load(); });

  const load = () => {
    api<Creation[]>("/api/creations/image").then(setItems).catch(() => {});
    api<Estimates>("/api/estimates").then(setEst).catch(() => {});
  };
  useEffect(() => {
    api<ToolOptions>("/api/tools/options").then(setOpts).catch((e) => setError(e.message));
    load();
  }, []);
  useEffect(() => save("featherbox.imageForm", form), [form]);

  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }));
  const model = form.model && opts?.image_models.some((m) => m.id === form.model) ? form.model : opts?.active.image || "";

  async function generate() {
    setError(null);
    try {
      await run("/api/tools/image", {
        prompt: form.prompt, negative: form.negative, model, aspect: form.aspect, style: form.style,
        count: form.count, seed: form.seed.trim() ? Number(form.seed) : -1,
      });
    } catch (e: any) {
      if (e.message !== "Cancelled") setError(e.message);
    }
    load();
  }

  async function remove(c: Creation) {
    if (!confirm("Delete this image?")) return;
    await api(`/api/creations/image/${c.id}`, { method: "DELETE" });
    if (open?.id === c.id) setOpen(null);
    load();
  }

  const reuse = (c: Creation) => set({ prompt: c.prompt || "", negative: c.negative || "", model: c.model || "",
    aspect: c.aspect || "portrait", style: c.style || "none", seed: String(c.seed ?? "") });

  if (!opts) return <PageLoading title="Image" subtitle="Describe a picture and your PC paints it. Nothing leaves your computer." error={error} />;
  if (!opts.image_models.length) {
    return (
      <div className="page">
        <header className="page-head"><h1>Image</h1></header>
        <div className="card empty">
          <ImagePlus size={28} />
          <h3>No image model yet</h3>
          <p className="muted">Download one in Models → Image (Z-Image Turbo or DreamShaper run well on 4 GB cards).</p>
          <button className="btn primary" onClick={goModels}>Open Models</button>
        </div>
      </div>
    );
  }

  return (
    <div className="page wide">
      <header className="page-head">
        <h1>Image</h1>
        <p className="muted">Describe a picture and your PC paints it. Nothing leaves your computer.</p>
      </header>
      {error && <div className="banner error"><span>{error}</span><button className="icon-btn" onClick={() => setError(null)}><X size={16} /></button></div>}

      <div className="tool">
        <aside className="card tool-form">
          <label className="lbl">Describe the image</label>
          <textarea rows={5} value={form.prompt} placeholder="e.g. A tea garden in Assam at sunrise, mist over the hills"
            onChange={(e) => set({ prompt: e.target.value })}
            onKeyDown={ctrlEnter(generate, !!form.prompt.trim() && !!model && !running)} />
          {!form.prompt && (
            <div className="chips tight">
              {IDEAS.map((i) => <button key={i} className="chip" onClick={() => set({ prompt: i })}>{i.split(",")[0]}</button>)}
            </div>
          )}

          <label className="lbl">Model</label>
          <select value={model} onChange={(e) => set({ model: e.target.value })}>
            {opts?.image_models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>

          <label className="lbl">Shape</label>
          <Segmented value={form.aspect} onChange={(aspect) => set({ aspect })}
            options={[["portrait", "Portrait 9:16"], ["square", "Square"], ["landscape", "Landscape 16:9"]]} />

          <label className="lbl">Style</label>
          <div className="styles">
            <button className={`style ${form.style === "none" ? "on" : ""}`} onClick={() => set({ style: "none" })}>
              <span className="swatch" style={{ background: "var(--card-2)" }} /> As written
            </button>
            {STYLES.map((st) => (
              <button key={st.id} className={`style ${form.style === st.id ? "on" : ""}`} onClick={() => set({ style: st.id })}>
                <span className="swatch" style={{ background: st.swatch }} /> {st.label}
              </button>
            ))}
          </div>

          <label className="lbl">How many</label>
          <Segmented value={form.count} onChange={(count) => set({ count })} options={[[1, "1"], [2, "2"], [3, "3"], [4, "4"]]} />

          <details className="adv">
            <summary className="lbl">Advanced</summary>
            <label className="lbl">Avoid (negative prompt)</label>
            <textarea rows={2} value={form.negative} placeholder="Leave empty for a sensible default" onChange={(e) => set({ negative: e.target.value })} />
            <label className="lbl">Seed (same seed + prompt = same image)</label>
            <input value={form.seed} placeholder="Random" inputMode="numeric" onChange={(e) => set({ seed: e.target.value.replace(/[^0-9]/g, "") })} />
          </details>

          {running ? <JobProgress job={job} onCancel={cancel} /> : (
            <button className="btn primary big full" disabled={!form.prompt.trim() || !model} onClick={generate} title="Ctrl+Enter">
              <Wand2 size={18} /> Create {form.count > 1 ? `${form.count} images` : "image"}
            </button>
          )}
          {est?.picture_s != null && model === opts?.active.image && (
            <p className="muted tiny">About {fmtAbout(est.picture_s).replace("about ", "")} per picture on this PC{form.count > 1 ? `, ${fmtAbout(est.picture_s * form.count)} for ${form.count}` : ""}.</p>
          )}
        </aside>

        <section className="gallery-wrap">
          <div className="row between">
            <h3>Your images <span className="muted small">· {items.length}</span></h3>
            {items.length > 0 && <button className="btn ghost small" onClick={() => post("/api/creations/image/reveal")}><FolderOpen size={14} /> Open folder</button>}
          </div>
          {!items.length && <p className="muted small">Images you create appear here, saved on your PC.</p>}
          <div className="gallery">
            {items.map((c) => (
              <figure key={c.id} className="creation card">
                <button className="creation-media" onClick={() => setOpen(c)} title="View larger">
                  <img src={c.url} alt={c.prompt} loading="lazy" style={{ aspectRatio: `${c.width} / ${c.height}` }} />
                </button>
                <figcaption>
                  <p className="clamp2 small">{c.prompt}</p>
                  <div className="muted tiny">{c.model_name} · {fmtSecs(c.seconds)} · {ago(c.created)}</div>
                  <div className="row wrap">
                    <a className="icon-btn" href={c.url} download={`featherbox-${c.id}.png`} title="Download"><Download size={15} /></a>
                    {canAnimate && <button className="icon-btn" title="Animate this picture" onClick={() => onAnimate(`image:${c.id}`)}><Clapperboard size={15} /></button>}
                    <button className="icon-btn" title="Use these settings again" onClick={() => reuse(c)}><RotateCcw size={15} /></button>
                    <button className="icon-btn" title="Delete" onClick={() => remove(c)}><Trash2 size={15} /></button>
                  </div>
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      </div>

      {open && (
        <div className="lightbox" onClick={() => setOpen(null)}>
          <img src={open.url} alt={open.prompt} onClick={(e) => e.stopPropagation()} />
          <div className="lightbox-bar" onClick={(e) => e.stopPropagation()}>
            <span className="small">{open.prompt}</span>
            <span className="muted tiny">{open.width}×{open.height} · seed {open.seed}</span>
            <a className="btn small" href={open.url} download={`featherbox-${open.id}.png`}><Download size={14} /> Download</a>
            {canAnimate && <button className="btn small" onClick={() => onAnimate(`image:${open.id}`)}><Clapperboard size={14} /> Animate</button>}
            <button className="icon-btn" onClick={() => setOpen(null)}><X size={16} /></button>
          </div>
        </div>
      )}
    </div>
  );
}
