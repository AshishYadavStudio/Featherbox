import { useEffect, useReducer, useRef, useState } from "react";
import { Eraser, Send, Square } from "lucide-react";
import { api, save, store, type Component, type SystemInfo } from "../api";

type Msg = { role: "user" | "assistant"; content: string };

const STARTERS = [
  "Give me 10 viral YouTube Shorts ideas about space",
  "Write a hook for a video on saving money as a student",
  "Suggest 5 catchy titles for a video about Indian street food",
];

// The conversation lives outside the page, so an answer keeps streaming in while you use another page.
const chat = {
  msgs: store<Msg[]>("featherbox.chat", []),
  busy: false,
  ctrl: null as AbortController | null,
  listeners: new Set<() => void>(),
};
function setChat(patch: Partial<Pick<typeof chat, "msgs" | "busy" | "ctrl">>) {
  Object.assign(chat, patch);
  if (patch.msgs) save("featherbox.chat", chat.msgs.slice(-60));
  chat.listeners.forEach((f) => f());
}
const setMsgs = (msgs: Msg[]) => setChat({ msgs });

export default function Chat({ sys, goModels }: { sys: SystemInfo | null; goModels: () => void }) {
  const [, rerender] = useReducer((x: number) => x + 1, 0);
  useEffect(() => { chat.listeners.add(rerender); return () => { chat.listeners.delete(rerender); }; }, []);
  const { msgs, busy } = chat;
  const [input, setInput] = useState("");
  const [models, setModels] = useState<Component[]>([]);
  const [model, setModel] = useState<string>(() => store("featherbox.chatModel", ""));
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => save("featherbox.chatModel", model), [model]);
  // braces matter: newer browsers return a Promise from scrollIntoView, which React would call as a cleanup
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);
  useEffect(() => {
    api<{ components: Component[] }>("/api/catalog").then((r) =>
      setModels(r.components.filter((c) => c.kind === "model" && c.category === "llm" && c.installed))).catch(() => {});
  }, [sys?.ready.llm]);

  async function send(text: string) {
    if (!text.trim() || busy) return;
    const history: Msg[] = [...msgs, { role: "user", content: text.trim() }];
    setMsgs([...history, { role: "assistant", content: "" }]);
    setInput("");
    const ctrl = new AbortController();
    setChat({ busy: true, ctrl });
    try {
      const r = await fetch("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal,
        body: JSON.stringify({
          model: model || null,
          messages: [{ role: "system", content: "You are Featherbox, a friendly assistant running privately on the user's own PC. Be concise and practical." }, ...history.slice(-20)],
        }),
      });
      if (!r.ok || !r.body) {
        const detail = await r.json().then((b) => b.detail).catch(() => r.statusText);
        throw new Error(detail);
      }
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let acc = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        acc += dec.decode(value, { stream: true });
        setMsgs([...history, { role: "assistant", content: acc }]);
      }
    } catch (e: any) {
      if (e.name !== "AbortError") setMsgs([...history, { role: "assistant", content: `⚠️ ${e.message}` }]);
    } finally {
      setChat({ busy: false, ctrl: null });
    }
  }

  if (sys && !sys.ready.llm) {
    return (
      <div className="page center">
        <div className="card empty">
          <h2>No text model yet</h2>
          <p className="muted">Install a text model to chat offline.</p>
          <button className="btn primary" onClick={goModels}>Open Models</button>
        </div>
      </div>
    );
  }

  return (
    <div className="page chat">
      <div className="row between chat-head">
        <h1>Chat</h1>
        <div className="row">
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">Default model</option>
            {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          <button className="icon-btn boxed" title="Clear chat" onClick={() => setMsgs([])} disabled={busy}><Eraser size={16} /></button>
        </div>
      </div>

      <div className="messages">
        {msgs.length === 0 && (
          <div className="starters">
            <p className="muted">Brainstorm ideas, hooks and titles. Runs fully on your PC.</p>
            {STARTERS.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.content || (busy && i === msgs.length - 1 ? <span className="typing"><i /><i /><i /></span> : "")}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
        <textarea
          rows={1} placeholder="Message Featherbox…" value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
        />
        {busy ? (
          <button type="button" className="btn" onClick={() => chat.ctrl?.abort()}><Square size={16} /> Stop</button>
        ) : (
          <button type="submit" className="btn primary" disabled={!input.trim()}><Send size={16} /></button>
        )}
      </form>
      <p className="muted tiny center-text">The first message loads the model into memory, which takes 10–30 s.</p>
    </div>
  );
}
