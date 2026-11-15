export type Hardware = {
  os: string; cpu: string; cores: number; threads: number; ram_gb: number; ram_free_gb: number;
  gpus: { vendor: string; name: string; vram_mb?: number; free_mb?: number }[];
  cuda_driver: number | null; vram_mb: number; vram_free_mb: number;
  recommended_backend: string; backend: string;
};

export type SystemInfo = {
  app: string; version: string; hardware: Hardware;
  settings: { backend: string; llm_model: string | null; image_model: string | null; voice: string; video_quality?: string };
  ready: { llm: boolean; image: boolean; tts: boolean; ffmpeg: boolean };
  llm_loaded: string | null;
  video_ready: boolean;
};

export type DownloadTask = {
  id: string; status: "queued" | "downloading" | "extracting" | "done" | "error" | "cancelled";
  done: number; total: number; speed: number; file: string; error: string | null;
};

export type Component = {
  id: string; kind: "engine" | "model"; category: "llm" | "image" | "video" | "tts" | "music" | "tool";
  name: string; description: string; size_mb: number; download_mb: number; license: string; backend: string | null;
  installed: boolean; fit: string; reason: string; tags: string[]; studio: boolean; soon: boolean;
  custom: boolean; url: string | null; download: DownloadTask | null;
};
export type Catalog = {
  components: Component[]; recommended: string[];
  active: { llm: string | null; image: string | null; video: string | null }; custom_kinds: Record<string, string>;
};

export type Job = {
  id: string; kind: string; title: string; status: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number; stage: string; error: string | null; result: any; created: number;
};

export type Scene = { narration: string; image_prompt: string };
export type Script = {
  title: string; scenes: Scene[]; description: string; hashtags: string[];
  topic?: string; tone?: string; language?: string;
};

export type Project = {
  id: string; title: string; status: string; duration: number | null; created: number;
  style: string; description: string; hashtags: string[]; video: string | null; thumb: string | null;
};

export type Voice = { id: string; label: string; lang: string };

export type SceneDetail = {
  index: number; narration: string; image_prompt: string; image: string; audio: string; duration: number; rev: number;
  motion: "ai" | "still"; clip: string | null;
};
export type ProjectDetail = {
  id: string; script: Script; voice: string; style: string; speed: number; captions: boolean;
  music: string; music_volume: number; motion: string; can_animate: boolean; status: string; scenes: SceneDetail[];
  project: string; video: string; thumb: string; duration: number | null;
};
export type MusicOption = { id: string; label: string };
export type MusicOptions = { moods: MusicOption[]; tracks: MusicOption[]; tone_mood: Record<string, string> };

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const body = await r.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* not json */ }
    throw new Error(msg || `Request failed (${r.status})`);
  }
  const type = r.headers.get("content-type") || "";
  return (type.includes("json") ? r.json() : r.text()) as Promise<T>;
}

export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export async function waitForJob(id: string, onUpdate: (j: Job) => void): Promise<Job> {
  for (;;) {
    const j = await api<Job>(`/api/jobs/${id}`);
    onUpdate(j);
    if (j.status === "done" || j.status === "error" || j.status === "cancelled") return j;
    await new Promise((r) => setTimeout(r, 800));
  }
}

export const fmtMB = (bytes: number) =>
  bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;

export const fmtSize = (mb: number) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`);

export function store<T>(key: string, fallback: T): T {
  try {
    // keys were "srijan.*" before the app was renamed to Featherbox: carry old values over once
    const raw = localStorage.getItem(key) ?? (key.startsWith("featherbox.") ? localStorage.getItem("srijan." + key.slice(11)) : null);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function save(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
}
