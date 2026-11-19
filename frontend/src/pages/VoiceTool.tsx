import { useEffect, useState } from "react";
import { AudioLines, Download, FolderOpen, Loader2, RotateCcw, Trash2, Wand2, X } from "lucide-react";
import { api, post, save, store, type Creation, type Voice } from "../api";
import { PageLoading, ago, ctrlEnter, fmtSecs } from "../components/ToolKit";

type Form = { text: string; voice: string; speed: number };
const EMPTY: Form = { text: "", voice: "af_heart", speed: 1 };
const LANGS: Record<string, string> = {
  "en-us": "English (US)", "en-gb": "English (UK)", hi: "Hindi", es: "Spanish", "fr-fr": "French",
  it: "Italian", "pt-br": "Portuguese (Brazil)", ja: "Japanese", cmn: "Chinese (Mandarin)",
};
const MAX = 5000;
const isDevanagari = (t: string) => /[ऀ-ॿ]/.test(t);

/** Text → natural speech, on the CPU (works on any PC, no GPU needed). */
export default function VoiceTool({ goModels }: { goModels: () => void }) {
  const [voices, setVoices] = useState<Voice[] | null>(null);
  const [form, setForm] = useState<Form>(() => ({ ...EMPTY, ...store<Partial<Form>>("featherbox.voiceForm", {}) }));
  const [items, setItems] = useState<Creation[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [latest, setLatest] = useState<string | null>(null);

  const load = () => api<Creation[]>("/api/creations/voice").then(setItems).catch(() => {});
  useEffect(() => {
    api<Voice[]>("/api/tts/voices").then(setVoices).catch((e) => { setVoices([]); setError(e.message); });
    load();
  }, []);
  useEffect(() => save("featherbox.voiceForm", form), [form]);

  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }));
  const groups = (voices ?? []).reduce<Record<string, Voice[]>>((g, v) => ((g[v.lang] ??= []).push(v), g), {});
  const voice = voices?.find((v) => v.id === form.voice);
  const hindiText = isDevanagari(form.text);
  const mismatch = voice && hindiText && voice.lang !== "hi";
  const firstHindi = voices?.find((v) => v.lang === "hi");

  async function generate() {
    setBusy(true); setError(null);
    try {
      const c = await post<Creation>("/api/tools/voice", { text: form.text, voice: form.voice, speed: form.speed });
      setLatest(c.id);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(c: Creation) {
    if (!confirm("Delete this recording?")) return;
    await api(`/api/creations/voice/${c.id}`, { method: "DELETE" });
    load();
  }

  if (!voices) return <PageLoading title="Voice" subtitle="Turn text into natural speech for voiceovers, narration or audiobooks. Runs on your processor, so any PC can do it." />;
  if (!voices.length) {
    return (
      <div className="page">
        <header className="page-head"><h1>Voice</h1></header>
        <div className="card empty">
          <AudioLines size={28} />
          <h3>Voice model not installed</h3>
          <p className="muted">{error || "Download Kokoro in Models → Voice (about 350 MB)."}</p>
          <button className="btn primary" onClick={goModels}>Open Models</button>
        </div>
      </div>
    );
  }

  return (
    <div className="page wide">
      <header className="page-head">
        <h1>Voice</h1>
        <p className="muted">Turn text into natural speech for voiceovers, narration or audiobooks. Runs on your processor, so any PC can do it.</p>
      </header>
      {error && <div className="banner error"><span>{error}</span><button className="icon-btn" onClick={() => setError(null)}><X size={16} /></button></div>}

      <div className="tool">
        <aside className="card tool-form">
          <div className="row between">
            <label className="lbl">Text</label>
            <span className={`tiny ${form.text.length > MAX ? "err" : "muted"}`}>{form.text.length} / {MAX}</span>
          </div>
          <textarea rows={9} value={form.text} placeholder="Type or paste what should be spoken. English or Hindi (हिंदी) both work."
            onChange={(e) => set({ text: e.target.value })}
            onKeyDown={ctrlEnter(generate, !busy && !!form.text.trim() && form.text.length <= MAX)} />
          {mismatch && firstHindi && (
            <p className="tiny" style={{ color: "var(--warn)" }}>
              This looks like Hindi. <button className="link" onClick={() => set({ voice: firstHindi.id })}>Use a Hindi voice</button>
            </p>
          )}

          <label className="lbl">Voice</label>
          <select value={form.voice} onChange={(e) => set({ voice: e.target.value })}>
            {Object.entries(groups).map(([lang, vs]) => (
              <optgroup key={lang} label={LANGS[lang] ?? lang}>
                {vs.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
              </optgroup>
            ))}
          </select>

          <label className="lbl">Speed · {form.speed.toFixed(2)}×</label>
          <input type="range" min={0.7} max={1.4} step={0.05} value={form.speed} onChange={(e) => set({ speed: Number(e.target.value) })} />

          <button className="btn primary big full" disabled={busy || !form.text.trim() || form.text.length > MAX} onClick={generate} title="Ctrl+Enter">
            {busy ? <Loader2 size={18} className="spin" /> : <Wand2 size={18} />} {busy ? "Speaking…" : "Create speech"}
          </button>
          <p className="muted tiny">Usually a few seconds; long texts take a little longer.</p>
        </aside>

        <section className="gallery-wrap">
          <div className="row between">
            <h3>Your recordings <span className="muted small">· {items.length}</span></h3>
            {items.length > 0 && <button className="btn ghost small" onClick={() => post("/api/creations/voice/reveal")}><FolderOpen size={14} /> Open folder</button>}
          </div>
          {!items.length && <p className="muted small">Recordings you create appear here, saved on your PC as WAV files.</p>}
          <div className="voice-list">
            {items.map((c) => (
              <div key={c.id} className={`card voice-item ${latest === c.id ? "fresh" : ""}`}>
                <p className="clamp2 small">{c.text}</p>
                <audio src={c.url} controls preload="none" autoPlay={latest === c.id} />
                <div className="row between wrap">
                  <span className="muted tiny">{c.voice_name} · {c.speed?.toFixed(2)}× · {fmtSecs(c.duration)} · {ago(c.created)}</span>
                  <div className="row">
                    <a className="icon-btn" href={c.url} download={`featherbox-${c.id}.wav`} title="Download"><Download size={15} /></a>
                    <button className="icon-btn" title="Use this text and voice again" onClick={() => set({ text: c.text || "", voice: c.voice || form.voice, speed: c.speed || 1 })}><RotateCcw size={15} /></button>
                    <button className="icon-btn" title="Delete" onClick={() => remove(c)}><Trash2 size={15} /></button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
