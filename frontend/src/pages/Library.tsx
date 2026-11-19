import { useEffect, useState } from "react";
import { Check, Copy, FolderOpen, SlidersHorizontal, Trash2, Wand2, X } from "lucide-react";
import { api, post, type Project } from "../api";
import { PageLoading } from "../components/ToolKit";

export default function Library({ goStudio, openProject }: { goStudio: () => void; openProject: (pid: string) => void }) {
  const [items, setItems] = useState<Project[] | null>(null);
  const [open, setOpen] = useState<Project | null>(null);
  const [copied, setCopied] = useState(false);

  const load = () => api<Project[]>("/api/projects").then(setItems).catch(() => setItems([]));
  useEffect(() => { load(); }, []);

  async function remove(p: Project) {
    if (!confirm(`Delete "${p.title}"? The video and all its files will be removed from disk.`)) return;
    await api(`/api/projects/${p.id}`, { method: "DELETE" });
    setOpen(null);
    load();
  }

  if (!items) return <PageLoading title="Library" subtitle="Every video you've made, stored on this PC." />;

  return (
    <div className="page">
      <header className="page-head row between">
        <div>
          <h1>Library</h1>
          <p className="muted">Every video you've made, stored on this PC.</p>
        </div>
        <button className="btn primary" onClick={goStudio}><Wand2 size={16} /> New video</button>
      </header>

      {items.length === 0 && (
        <div className="card empty">
          <h2>No videos yet</h2>
          <p className="muted">Your finished videos will appear here.</p>
        </div>
      )}

      <div className="grid">
        {items.map((p) => (
          <button key={p.id} className="tile" onClick={() => setOpen(p)}>
            <div className="tile-media">
              {p.thumb ? <img src={p.thumb} alt="" loading="lazy" /> : <div className="tile-ph">{p.status === "rendering" ? "Unfinished" : "No preview"}</div>}
              {p.duration && <span className="tile-dur">{Math.round(p.duration)}s</span>}
            </div>
            <div className="tile-title">{p.title}</div>
            <div className="muted tiny">{new Date(p.created * 1000).toLocaleString()} · {p.style}</div>
          </button>
        ))}
      </div>

      {open && (
        <div className="modal" onClick={() => setOpen(null)}>
          <div className="modal-body card" onClick={(e) => e.stopPropagation()}>
            <button className="icon-btn close" onClick={() => setOpen(null)}><X size={18} /></button>
            <div className="done">
              <div className="phone">{open.video ? <video src={open.video} controls autoPlay playsInline /> : <div className="tile-ph">Not rendered</div>}</div>
              <div className="done-side">
                <h2>{open.title}</h2>
                <p>{open.description}</p>
                <p className="muted">{open.hashtags.map((h) => "#" + h).join(" ")}</p>
                <div className="row wrap">
                  <button className="btn" onClick={() => {
                    navigator.clipboard.writeText(`${open.title}\n\n${open.description}\n\n${open.hashtags.map((h) => "#" + h).join(" ")}`);
                    setCopied(true); setTimeout(() => setCopied(false), 1500);
                  }}>{copied ? <Check size={16} /> : <Copy size={16} />} Copy caption</button>
                  {open.video && <a className="btn primary" href={open.video} download={`${open.title}.mp4`}>Download MP4</a>}
                  {open.status === "done" && <button className="btn" onClick={() => openProject(open.id)}><SlidersHorizontal size={16} /> Edit scenes &amp; music</button>}
                  <button className="btn" onClick={() => post(`/api/projects/${open.id}/reveal`)}><FolderOpen size={16} /> Open folder</button>
                  <button className="btn danger" onClick={() => remove(open)}><Trash2 size={16} /> Delete</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
