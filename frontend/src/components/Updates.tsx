import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Download, ExternalLink, Loader2, RefreshCw, Sparkles, X } from "lucide-react";
import { api, post, save, store } from "../api";

export type UpdateStatus = {
  current: string; available: boolean; newest_seen: string | null; enabled: boolean; last_check: number | null;
  state: "idle" | "checking" | "downloading" | "verifying" | "installing" | "error"; progress: number;
  error: string | null; can_install: boolean; reason: string | null;
  latest: { version: string; name: string; notes: string; url: string; published: string; size: number | null } | null;
};

const BUSY = ["downloading", "verifying", "installing"];

/** Follows the update status; while an update installs, waits for the new version and reloads the page. */
function useUpdate(pollMs: number) {
  const [st, setSt] = useState<UpdateStatus | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [slow, setSlow] = useState(false);
  const target = useRef<string | null>(null);

  const load = () => api<UpdateStatus>("/api/update").then((s) => { setSt(s); return s; });
  useEffect(() => {
    load().catch(() => {});
    const t = setInterval(() => { if (!target.current) load().catch(() => {}); }, pollMs);
    const kick = () => load().catch(() => {});      // another part of the page started or checked an update
    window.addEventListener("featherbox:update", kick);
    return () => { clearInterval(t); window.removeEventListener("featherbox:update", kick); };
  }, [pollMs]); // eslint-disable-line react-hooks/exhaustive-deps

  // fast polling while downloading / installing
  useEffect(() => {
    if (!st || !BUSY.includes(st.state)) return;
    if (st.state === "installing" && st.latest) {
      target.current = st.latest.version;
      setRestarting(true);
      return;
    }
    const t = setTimeout(() => load().catch(() => {}), 700);
    return () => clearTimeout(t);
  }, [st]); // eslint-disable-line react-hooks/exhaustive-deps

  // the app goes away while setup replaces it; reload once the new version answers
  useEffect(() => {
    if (!restarting) return;
    const late = setTimeout(() => setSlow(true), 3 * 60 * 1000);
    const t = setInterval(() => {
      api<{ version: string }>("/api/system")
        .then((s) => { if (s.version === target.current) location.reload(); })
        .catch(() => {});
    }, 2000);
    return () => { clearInterval(t); clearTimeout(late); };
  }, [restarting]);

  const install = async () => {
    try { setSt(await post<UpdateStatus>("/api/update/install")); window.dispatchEvent(new Event("featherbox:update")); } catch (e: any) { setSt((s) => s && { ...s, state: "error", error: e.message }); }
  };
  const check = async () => {
    setSt((s) => s && { ...s, state: "checking" });
    try { setSt(await post<UpdateStatus>("/api/update/check")); window.dispatchEvent(new Event("featherbox:update")); } catch { load().catch(() => {}); }
  };
  return { st, setSt, restarting, slow, install, check, load };
}

/** Banner shown on every page when a newer Featherbox is out. */
export function UpdateBanner({ onRestarting }: { onRestarting: (v: boolean) => void }) {
  const { st, restarting, slow, install } = useUpdate(30 * 60 * 1000);
  const [dismissed, setDismissed] = useState(() => store<string | null>("featherbox.updateDismissed", null));
  const [notes, setNotes] = useState(false);
  useEffect(() => onRestarting(restarting), [restarting]); // eslint-disable-line react-hooks/exhaustive-deps

  if (restarting && st?.latest) {
    return (
      <div className="update-overlay">
        <div className="card">
          <Loader2 size={28} className="spin" />
          <h3>Installing Featherbox {st.latest.version}…</h3>
          <p className="muted small">Featherbox closes, updates and starts again by itself. This window reloads when it's ready (usually under a minute). Your models and creations are kept.</p>
          {slow && <p className="small" style={{ color: "var(--warn)" }}>This is taking longer than expected. If Featherbox doesn't come back, open it from the Start menu; you can run the update again from there.</p>}
        </div>
      </div>
    );
  }
  if (!st?.available || !st.latest) return null;
  const busy = BUSY.includes(st.state);
  if (dismissed === st.latest.version && !busy && st.state !== "error") return null;

  return (
    <>
      <div className="banner update">
        <Sparkles size={18} />
        <div className="update-text">
          <b>Featherbox {st.latest.version} is available</b> <span className="muted">· you have {st.current}</span>
          {busy && (
            <div className="update-progress">
              <div className="bar"><div style={{ width: `${Math.round(st.progress * 100)}%` }} /></div>
              <span className="tiny muted">{st.state === "verifying" ? "Checking the download…" : `Downloading… ${Math.round(st.progress * 100)}%`}</span>
            </div>
          )}
          {st.state === "error" && <div className="tiny err">{st.error}</div>}
          {!busy && st.reason && <div className="tiny muted">{st.reason}</div>}
        </div>
        <div className="row">
          <button className="btn small ghost" onClick={() => setNotes(true)}>What's new</button>
          {st.can_install ? (
            <button className="btn small primary" disabled={busy} onClick={install}>
              {busy ? <Loader2 size={14} className="spin" /> : <Download size={14} />} {st.state === "error" ? "Try again" : "Update now"}
            </button>
          ) : !st.reason?.startsWith("Something") && !st.reason?.startsWith("A model") ? (
            <a className="btn small primary" href={st.latest.url} target="_blank" rel="noreferrer"><Download size={14} /> Download</a>
          ) : null}
          {!busy && (
            <button className="icon-btn" title="Remind me later" onClick={() => { save("featherbox.updateDismissed", st.latest!.version); setDismissed(st.latest!.version); }}>
              <X size={16} />
            </button>
          )}
        </div>
      </div>
      {notes && (
        <div className="lightbox" onClick={() => setNotes(false)}>
          <div className="card notes" onClick={(e) => e.stopPropagation()}>
            <div className="row between">
              <h3>What's new in {st.latest.version}</h3>
              <button className="icon-btn" onClick={() => setNotes(false)}><X size={16} /></button>
            </div>
            <div className="notes-body">{st.latest.notes || "No release notes."}</div>
            <a className="small" href={st.latest.url} target="_blank" rel="noreferrer">Open on GitHub <ExternalLink size={12} /></a>
          </div>
        </div>
      )}
    </>
  );
}

/** Updates section on the Models page: version, check now, automatic checks on/off. */
export function UpdatesCard() {
  const { st, setSt, check, install } = useUpdate(60 * 1000);
  if (!st) return null;
  const busy = BUSY.includes(st.state);
  const toggle = async (on: boolean) => {
    setSt({ ...st, enabled: on });
    await api("/api/settings", { method: "PATCH", body: JSON.stringify({ update_check: on }) }).catch(() => {});
  };
  const when = st.last_check ? new Date(st.last_check * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : null;
  return (
    <section className="card updates">
      <div className="row between wrap">
        <div>
          <h3>Featherbox {st.current}</h3>
          <p className="muted small">
            {st.state === "checking" ? "Checking for updates…"
              : st.available && st.latest ? <>Version <b>{st.latest.version}</b> is available.</>
              : st.newest_seen ? <><CheckCircle2 size={13} className="ok-ic" /> You have the latest version{when ? ` (checked ${when})` : ""}.</>
              : st.error || "Not checked yet."}
          </p>
        </div>
        <div className="row">
          {st.available && st.can_install && (
            <button className="btn small primary" disabled={busy} onClick={install}>
              {busy ? <Loader2 size={14} className="spin" /> : <Download size={14} />} {busy ? `${Math.round(st.progress * 100)}%` : "Update now"}
            </button>
          )}
          <button className="btn small" disabled={st.state === "checking" || busy} onClick={check}>
            <RefreshCw size={14} className={st.state === "checking" ? "spin" : ""} /> Check now
          </button>
        </div>
      </div>
      <label className="toggle small">
        <input type="checkbox" checked={st.enabled} onChange={(e) => toggle(e.target.checked)} />
        Check for updates automatically (asks GitHub about once a day; nothing about you or your files is sent)
      </label>
    </section>
  );
}
