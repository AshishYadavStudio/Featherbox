import { useEffect, useRef, useState } from "react";
import { Loader2, Pause, Upload, Volume2 } from "lucide-react";
import { api, type MusicOptions } from "../api";

/** Background music choice: auto (from tone), a built-in mood, the creator's own track, or none. */
export default function MusicPicker({ value, volume, tone, onChange }: {
  value: string; volume: number; tone?: string; onChange: (value: string, volume: number) => void;
}) {
  const [opts, setOpts] = useState<MusicOptions | null>(null);
  const [playing, setPlaying] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => { api<MusicOptions>("/api/music").then(setOpts).catch(() => {}); }, []);
  useEffect(() => () => audio.current?.pause(), []);

  const autoMood = opts?.tone_mood[tone || ""] || "calm";
  const mood = value === "auto" ? autoMood : value.startsWith("mood:") ? value.slice(5) : null;
  const autoLabel = opts?.moods.find((m) => m.id === `mood:${autoMood}`)?.label;

  function togglePreview() {
    if (playing) { audio.current?.pause(); setPlaying(false); return; }
    if (!mood) return;
    const a = new Audio(`/api/music/preview/${mood}`);
    a.volume = 0.6;
    a.onended = () => setPlaying(false);
    audio.current = a;
    setPlaying(true);
    a.play().catch(() => setPlaying(false));
  }

  async function upload(f: File) {
    setUploading(true); setErr(null);
    try {
      const r = await fetch(`/api/music/upload?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || "Upload failed");
      setOpts((o) => o && { ...o, tracks: [...o.tracks.filter((t) => t.id !== body.id), body] });
      onChange(body.id, volume);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setUploading(false);
      if (file.current) file.current.value = "";
    }
  }

  return (
    <div className="music-picker">
      <div className="row">
        <select value={value} onChange={(e) => { audio.current?.pause(); setPlaying(false); onChange(e.target.value, volume); }}>
          <option value="auto">Auto{autoLabel ? ` · ${autoLabel}` : ""} (matches tone)</option>
          <option value="none">No music</option>
          <optgroup label="Built-in moods">
            {opts?.moods.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </optgroup>
          {!!opts?.tracks.length && (
            <optgroup label="Your tracks">
              {opts.tracks.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
            </optgroup>
          )}
        </select>
        <button className="icon-btn boxed" title={playing ? "Stop preview" : "Preview mood"} disabled={!mood} onClick={togglePreview}>
          {playing ? <Pause size={16} /> : <Volume2 size={16} />}
        </button>
        <button className="icon-btn boxed" title="Add your own music (mp3, wav, m4a…)" disabled={uploading} onClick={() => file.current?.click()}>
          {uploading ? <Loader2 size={16} className="spin" /> : <Upload size={16} />}
        </button>
        <input ref={file} type="file" accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac" hidden
          onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      </div>
      {value !== "none" && (
        <>
          <label className="lbl">Music volume · {Math.round(volume * 100)}%</label>
          <input type="range" min={0.1} max={1} step={0.05} value={volume} onChange={(e) => onChange(value, Number(e.target.value))} />
          <p className="muted tiny">Music dips automatically while the voice is speaking.</p>
        </>
      )}
      {err && <p className="tiny" style={{ color: "var(--bad)" }}>{err}</p>}
    </div>
  );
}
