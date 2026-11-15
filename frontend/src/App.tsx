import { useEffect, useState } from "react";
import { AudioLines, Clapperboard, Cpu, Feather, Film, HardDriveDownload, Image as ImageIcon, LibraryBig, MessageSquare } from "lucide-react";
import { api, store, save, type SystemInfo } from "./api";
import Studio from "./pages/Studio";
import Models from "./pages/Models";
import Chat from "./pages/Chat";
import Library from "./pages/Library";
import ImageTool from "./pages/ImageTool";
import VideoTool from "./pages/VideoTool";
import VoiceTool from "./pages/VoiceTool";

export type Page = "studio" | "image" | "video" | "voice" | "chat" | "library" | "models";

const NAV: { id: Page; label: string; icon: typeof Clapperboard }[] = [
  { id: "studio", label: "Studio", icon: Clapperboard },
  { id: "image", label: "Image", icon: ImageIcon },
  { id: "video", label: "Video", icon: Film },
  { id: "voice", label: "Voice", icon: AudioLines },
  { id: "library", label: "Library", icon: LibraryBig },
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "models", label: "Models", icon: HardDriveDownload },
];

export default function App() {
  const [page, setPage] = useState<Page>(() => store<Page>("featherbox.page", "studio"));
  const [sys, setSys] = useState<SystemInfo | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => save("featherbox.page", page), [page]);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api<SystemInfo>("/api/system")
        .then((s) => { if (alive) { setSys(s); setOffline(false); } })
        .catch(() => alive && setOffline(true));
    load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  // open a finished project in Studio's done view (Studio reads its state from localStorage on mount)
  const openProject = (pid: string) => {
    save("featherbox.result", { project: pid });
    save("featherbox.stage", "done");
    setPage("studio");
  };

  const hw = sys?.hardware;
  const gpu = hw?.gpus.find((g) => g.vram_mb) ?? hw?.gpus[0];
  const allReady = sys ? Object.values(sys.ready).every(Boolean) : false;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Feather size={18} /></div>
          <div>
            <div className="brand-name">Featherbox</div>
            <div className="brand-sub">Local AI, light on your PC</div>
          </div>
        </div>
        <nav>
          {NAV.map(({ id, label, icon: Icon }) => (
            <button key={id} className={`nav-item ${page === id ? "active" : ""}`} onClick={() => setPage(id)}>
              <Icon size={18} />
              <span>{label}</span>
              {id === "models" && sys && !allReady && <span className="dot" title="Setup needed" />}
            </button>
          ))}
        </nav>
        <div className="hw-chip" title={hw ? `${hw.cpu}\n${hw.ram_gb} GB RAM` : ""}>
          <Cpu size={16} />
          <div>
            <div className="hw-main">{offline ? "Backend offline" : gpu ? gpu.name.replace("NVIDIA GeForce ", "") : hw ? "CPU only" : "Detecting…"}</div>
            {hw && !offline && (
              <div className="hw-sub">
                {hw.vram_mb ? `${(hw.vram_mb / 1024).toFixed(0)} GB VRAM · ` : ""}{hw.ram_gb} GB RAM · {hw.backend.toUpperCase()}
              </div>
            )}
          </div>
        </div>
      </aside>
      <main className="content">
        {offline && (
          <div className="banner error">Can't reach the Featherbox backend. Start it with <code>start.bat</code>.</div>
        )}
        {page === "studio" && <Studio sys={sys} goModels={() => setPage("models")} goLibrary={() => setPage("library")} />}
        {page === "image" && <ImageTool goModels={() => setPage("models")} canAnimate={!!sys?.video_ready}
          onAnimate={(ref) => { save("featherbox.videoStart", ref); setPage("video"); }} />}
        {page === "video" && <VideoTool goModels={() => setPage("models")} />}
        {page === "voice" && <VoiceTool goModels={() => setPage("models")} />}
        {page === "library" && <Library goStudio={() => setPage("studio")} openProject={openProject} />}
        {page === "chat" && <Chat sys={sys} goModels={() => setPage("models")} />}
        {page === "models" && <Models sys={sys} />}
      </main>
    </div>
  );
}
