import { useCallback, useEffect, useState } from "react";
import { Check, Download, ExternalLink, Link2, Loader2, Plus, Rocket, Trash2, X } from "lucide-react";
import { UpdatesCard } from "../components/Updates";
import { api, fmtMB, fmtSize, post, type Catalog, type Component, type SystemInfo } from "../api";

/** Advice only: every model can still be downloaded. */
const FIT: Record<string, [string, string]> = {
  great: ["Runs well", "ok"],
  slow: ["Runs slowly", "warn"],
  "very-slow": ["Very slow", "warn"],
  "too-big": ["Probably won't run", "bad"],
  recommended: ["Best for your PC", "ok"],
  "other-backend": ["For other hardware", "dim"],
  soon: ["Coming soon", "dim"],
};

type Tab = "llm" | "image" | "video" | "tts" | "stt" | "music" | "engine";
const TABS: { id: Tab; label: string; blurb: string }[] = [
  { id: "llm", label: "Text", blurb: "Writes your scripts, titles and captions, and powers Chat." },
  { id: "image", label: "Image", blurb: "Paints a picture for every scene." },
  { id: "video", label: "Video", blurb: "Brings scenes to life with short AI motion clips (optional)." },
  { id: "tts", label: "Voice", blurb: "Reads the script aloud as the voiceover." },
  { id: "stt", label: "Speech to text", blurb: "Writes down what is said in a recording or video (Transcribe page)." },
  { id: "music", label: "Music", blurb: "Background music under the voice." },
  { id: "engine", label: "Engines", blurb: "The programs that run the models on your CPU or GPU." },
];
const inTab = (c: Component, t: Tab) => (t === "engine" ? c.kind === "engine" : c.kind === "model" && c.category === t);

export default function Models({ sys }: { sys: SystemInfo | null }) {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [tab, setTab] = useState<Tab>("llm");
  const [onlyGood, setOnlyGood] = useState(false);
  const [showAllEngines, setShowAllEngines] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() =>
    api<Catalog>("/api/catalog").then(setCat).catch((e) => setError(e.message)), []);

  const items = cat?.components ?? [];
  const recommended = cat?.recommended ?? [];
  const active = items.some((c) => c.download && ["queued", "downloading", "extracting"].includes(c.download.status));
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(load, active ? 1000 : 5000); return () => clearInterval(t); }, [load, active]);

  const missing = items.filter((c) => recommended.includes(c.id) && !c.installed);
  const missingMB = missing.reduce((n, c) => n + c.download_mb, 0);
  const hw = sys?.hardware;

  const act = async (fn: () => Promise<unknown>) => { setError(null); try { await fn(); } catch (e: any) { setError(e.message); } load(); };

  let list = items.filter((c) => inTab(c, tab));
  if (tab === "engine" && !showAllEngines) list = list.filter((c) => c.fit !== "other-backend" || c.installed);
  if (onlyGood && tab !== "engine") list = list.filter((c) => c.installed || ["great", "recommended"].includes(c.fit));
  const meta = TABS.find((t) => t.id === tab)!;
  const inUse = tab === "llm" || tab === "image" || tab === "video" || tab === "stt" ? cat?.active[tab] : null;
  const inUseName = items.find((c) => c.id === inUse)?.name;

  return (
    <div className="page">
      <header className="page-head">
        <h1>Models</h1>
        <p className="muted">Download engines and AI models once. After that everything works offline.</p>
      </header>
      {error && (
        <div className="banner error">
          <span>{error}</span>
          <button className="icon-btn" onClick={() => setError(null)}><X size={16} /></button>
        </div>
      )}

      <section className="setup card">
        <div className="setup-hw">
          <Spec k="Processor" v={hw ? hw.cpu : "…"} />
          <Spec k="Memory" v={hw ? `${hw.ram_gb} GB RAM` : "…"} />
          <Spec k="Graphics" v={hw ? (hw.gpus.map((g) => g.name + (g.vram_mb ? ` (${(g.vram_mb / 1024).toFixed(0)} GB)` : "")).join(", ") || "None detected") : "…"} />
          <div className="spec">
            <div className="lbl">Compute backend</div>
            <select value={sys?.settings.backend ?? "auto"} onChange={(e) => act(() => api("/api/settings", { method: "PATCH", body: JSON.stringify({ backend: e.target.value }) }))}>
              <option value="auto">Auto ({hw?.recommended_backend.toUpperCase() ?? "…"})</option>
              <option value="cuda">NVIDIA CUDA</option>
              <option value="vulkan">Vulkan (AMD / Intel / NVIDIA)</option>
              <option value="cpu">CPU only</option>
            </select>
          </div>
        </div>
        <div className="setup-cta">
          {missing.length === 0 && items.length > 0 ? (
            <div className="eyebrow ok"><Check size={14} /> Everything your PC needs is installed</div>
          ) : (
            <>
              <div>
                <h3>One-click setup</h3>
                <p className="muted small">Installs the best engine and models for this PC: {missing.map((c) => c.name).join(", ") || "…"}.</p>
              </div>
              <button className="btn primary big" disabled={!missing.length || active} onClick={() => act(() => post("/api/setup"))}>
                {active ? <Loader2 size={18} className="spin" /> : <Rocket size={18} />} {active ? "Installing…" : `Install (${fmtSize(missingMB)})`}
              </button>
            </>
          )}
        </div>
      </section>

      <div className="model-tabs">
        <div className="chips">
          {TABS.map((t) => {
            const all = items.filter((c) => inTab(c, t.id) && !c.soon);
            const have = all.filter((c) => c.installed).length;
            return (
              <button key={t.id} className={`chip ${tab === t.id ? "on" : ""}`} onClick={() => setTab(t.id)}>
                {t.label} <span className="chip-count">{all.length ? `${have}/${all.length}` : "soon"}</span>
              </button>
            );
          })}
        </div>
        {tab !== "engine" && (
          <label className="toggle small">
            <input type="checkbox" checked={onlyGood} onChange={(e) => setOnlyGood(e.target.checked)} />
            Only show models that run well here
          </label>
        )}
      </div>

      <section className="group">
        <div className="row between wrap">
          <div>
            <h3>{meta.label} models</h3>
            <p className="muted small">{meta.blurb}</p>
          </div>
          {inUseName && <span className="badge ok"><Check size={12} /> {tab === "stt" ? "Transcribe uses" : "Studio uses"}: {inUseName}</span>}
          {tab === "engine" && (
            <button className="btn ghost small" onClick={() => setShowAllEngines((v) => !v)}>{showAllEngines ? "Hide other hardware" : "Show all engines"}</button>
          )}
        </div>
        {tab === "video" && (
          <div className="banner"><span>Video models add <b>AI motion</b> to scenes: pick "AI motion" under Motion in the Studio, or press Animate on any scene of a finished video. They are optional; without one, scenes use a slow zoom and pan.</span></div>
        )}
        {tab === "stt" && !items.find((c) => c.id === "whisper-cpu")?.installed && (
          <div className="banner"><span>These models need the <b>whisper.cpp</b> engine (9 MB, runs on the CPU).</span>
            <button className="btn small" onClick={() => act(() => post("/api/catalog/whisper-cpu/install"))}><Download size={14} /> Get engine</button></div>
        )}
        {tab === "music" && (
          <div className="banner"><span>The 5 built-in music moods (Calm, Uplifting, Playful, Mysterious, Epic) are always available and need no download. You can also add your own tracks in the Studio.</span></div>
        )}
        <div className="model-list">
          {list.map((c) => (
            <ModelRow key={c.id} c={c} recommended={recommended.includes(c.id)} inUse={c.id === inUse} act={act} />
          ))}
          {!list.length && <p className="muted small">Nothing here runs well on this PC. Untick the filter to see every model.</p>}
        </div>
        {(tab === "llm" || tab === "image") && <AddCustom kinds={cat?.custom_kinds ?? {}} tab={tab} act={act} />}
      </section>

      <UpdatesCard />
    </div>
  );
}

function confirmDownload(c: Component): boolean {
  const size = fmtSize(c.download_mb);
  if (c.fit === "too-big") return confirm(`${c.name} probably won't run on this PC.\n${c.reason}\n\nDownload ${size} anyway?`);
  if (c.fit === "very-slow") return confirm(`${c.name} will be very slow on this PC.\n${c.reason}\n\nDownload ${size} anyway?`);
  if (!c.studio) return confirm(`The Studio can't use ${c.name} yet (coming in a later update).\n\nDownload ${size} now anyway?`);
  return true;
}

function ModelRow({ c, recommended, inUse, act }: {
  c: Component; recommended: boolean; inUse: boolean; act: (fn: () => Promise<unknown>) => void;
}) {
  const d = c.download;
  const busy = d && ["queued", "downloading", "extracting"].includes(d.status);
  const pct = d && d.total ? Math.min(100, (d.done / d.total) * 100) : 0;
  const [fitText, fitTone] = FIT[c.fit] ?? [c.fit, "dim"];
  const selectable = c.kind === "model" && c.studio && ["llm", "image", "video", "stt"].includes(c.category);
  const shared = !c.installed && c.download_mb < c.size_mb;
  const settingKey = ({ llm: "llm_model", video: "video_model", stt: "stt_model" } as Record<string, string>)[c.category] ?? "image_model";
  return (
    <div className={`model card ${c.installed ? "installed" : ""} ${c.soon ? "soon" : ""}`}>
      <div className="model-info">
        <div className="row wrap">
          <strong>{c.name}</strong>
          {recommended && !c.installed && <span className="badge accent">Recommended</span>}
          {c.tags.map((t) => <span key={t} className="badge">{t}</span>)}
          {c.custom && <span className="badge">Added by you</span>}
          {!c.studio && !c.soon && <span className="badge">Not used by Studio yet</span>}
        </div>
        <div className="muted small">{c.description}</div>
        {c.fit !== "recommended" && (
          <div className={`fit fit-${fitTone}`}>
            <span className={`badge ${fitTone}`}>{fitText}</span>
            <span className="small">{c.reason}</span>
          </div>
        )}
        {!c.soon && (
          <div className="muted tiny">
            {shared ? `${fmtSize(c.download_mb)} to download (${fmtSize(c.size_mb)} total, shares files you already have)` : fmtSize(c.size_mb)}
            {" · "}{c.license}
            {c.url && <> · <a href={c.url.replace("/resolve/", "/blob/")} target="_blank" rel="noreferrer">Model page <ExternalLink size={11} /></a></>}
          </div>
        )}
        {busy && d && (
          <div className="dl">
            <div className="bar"><div style={{ width: `${pct}%` }} /></div>
            <div className="muted tiny">
              {d.status === "extracting" ? "Unpacking…" : d.status === "queued" ? "Starting…" : `${fmtMB(d.done)} of ${fmtMB(d.total)} · ${fmtMB(d.speed)}/s · ${d.file}`}
            </div>
          </div>
        )}
        {d?.status === "error" && <div className="err tiny">{d.error}</div>}
      </div>
      <div className="model-act">
        {c.soon ? null : c.installed ? (
          <>
            {selectable && (inUse
              ? <span className="badge ok"><Check size={12} /> In use</span>
              : <button className="btn small" onClick={() => act(() => api("/api/settings", { method: "PATCH", body: JSON.stringify({ [settingKey]: c.id }) }))}>Use this</button>)}
            {!selectable && <span className="badge ok"><Check size={12} /> Installed</span>}
            {!(c.id === "ffmpeg" && !d) && (
              <button className="icon-btn" title="Remove" onClick={() => {
                if (confirm(`Remove ${c.name}?${c.custom ? " It will also be taken off this list." : " You can download it again later."}`))
                  act(() => api(`/api/catalog/${c.id}`, { method: "DELETE" }));
              }}>
                <Trash2 size={15} />
              </button>
            )}
          </>
        ) : busy ? (
          <button className="btn small" onClick={() => act(() => post(`/api/catalog/${c.id}/cancel`))}><X size={14} /> Pause</button>
        ) : (
          <>
            <button className="btn small" onClick={() => confirmDownload(c) && act(() => post(`/api/catalog/${c.id}/install`))}>
              <Download size={14} /> {d?.status === "cancelled" || d?.status === "error" ? "Resume" : "Download"}
            </button>
            {c.custom && (
              <button className="icon-btn" title="Take off the list" onClick={() => act(() => api(`/api/catalog/${c.id}`, { method: "DELETE" }))}>
                <X size={15} />
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function AddCustom({ kinds, tab, act }: { kinds: Record<string, string>; tab: "llm" | "image"; act: (fn: () => Promise<unknown>) => void }) {
  const options = Object.entries(kinds).filter(([k]) => (tab === "llm" ? k === "llm" : k !== "llm"));
  const [url, setUrl] = useState("");
  const [kind, setKind] = useState(options[0]?.[0] ?? "llm");
  const [adding, setAdding] = useState(false);
  useEffect(() => { setKind(tab === "llm" ? "llm" : "sd15"); }, [tab]);
  const add = async () => {
    setAdding(true);
    await act(async () => { await post("/api/catalog/custom", { url, kind }); setUrl(""); });
    setAdding(false);
  };
  return (
    <div className="card add-custom">
      <div>
        <h3><Link2 size={16} /> Add your own {tab === "llm" ? "text" : "image"} model</h3>
        <p className="muted small">
          Paste the link to one file on Hugging Face (open the model, go to <b>Files</b>, click the file, copy the address).
          {tab === "llm" ? " Any .gguf chat model works." : " SD 1.5 and SDXL checkpoints in .safetensors work."}
        </p>
      </div>
      <div className="row wrap">
        <input className="grow" placeholder="https://huggingface.co/…/blob/main/model.gguf" value={url} onChange={(e) => setUrl(e.target.value)} />
        {options.length > 1 && (
          <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: "auto" }}>
            {options.map(([k, label]) => <option key={k} value={k}>{label.replace("Image model: ", "")}</option>)}
          </select>
        )}
        <button className="btn primary" disabled={!url.trim() || adding} onClick={add}>
          {adding ? <Loader2 size={16} className="spin" /> : <Plus size={16} />} Add to list
        </button>
      </div>
    </div>
  );
}

function Spec({ k, v }: { k: string; v: string }) {
  return (
    <div className="spec">
      <div className="lbl">{k}</div>
      <div className="spec-v">{v}</div>
    </div>
  );
}
