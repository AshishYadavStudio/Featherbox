import { useEffect, useRef, useState } from "react";
import { SCENE_SVG } from "./launchScene";

/**
 * Launch animation (about 2 s): a clay feather from the logo falls onto a sad clay laptop, is drawn into its
 * screen, and the laptop comes alive with the Featherbox logo. Claymation: 12 fps stop motion, clay edges "boil".
 *
 * Motion is simulated, not keyframed:
 *  - the swing: a thin light plate falling in air (Andersen, Pesavento & Wang, J. Fluid Mech. 541, 2005)
 *  - the twirl: spin around the quill driven by airspeed (autorotation of the lopsided vane), simplified
 *  - the spiral: the feather dragged by air swirling into the screen (swirl + inflow, capped wind speed)
 *
 * Plays once per app window, over the app while it loads. A click or key skips it; with "reduce motion"
 * on, it shows the last frame briefly instead. The artwork keeps a fixed size (laptop ~170 px wide).
 */

const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const seg = (t: number, a: number, b: number) => clamp((t - a) / (b - a));
const smooth = (p: number) => p * p * (3 - 2 * p);
const easeOut = (p: number) => 1 - (1 - p) ** 3;
const spring = (p: number, z = 5.5, f = 9) => (p <= 0 ? 0 : 1 - Math.exp(-z * p) * Math.cos(f * p));
const lerp = (a: number, b: number, p: number) => a + (b - a) * p;
const rgb = (h: string) => h.match(/\w\w/g)!.map((x) => parseInt(x, 16));
const mix = (a: string, b: string, p: number) => {
  const A = rgb(a), B = rgb(b);
  return `rgb(${A.map((v, i) => Math.round(lerp(v, B[i], p))).join(",")})`;
};
const DEG = 180 / Math.PI;

const FALL_S = 1.25, ND_PER_S = 12, HZ = 600, FPS = 12;
const SCREEN = { x: 0, y: 20 };
const SPIN_PER_SPEED = 0.028, SPIN_LAG = 0.22;
const LAPTOP_UNITS = 116, LAPTOP_PX = 170;
const HOLD_S = 0.25, FADE_MS = 320;

type Sample = { x: number; y: number; spine: number; s: number; phi: number };

/** Simulate the whole feather path once (a few milliseconds). */
function simulate(): { out: Sample[]; merge: number } {
  // 1. the swing: falling-plate model, nondimensional, body frame (u along, v normal, w spin)
  const I = 0.25, beta = 0.1, A = 1.4, B = 1.0, CT = 1.2, CR = Math.PI, mu1 = 0.2, mu2 = 0.2, dt = 0.001;
  const mU = I + beta * beta, mV = I + 1, It = 0.25 * (I * (1 + beta * beta) + 0.5 * (1 - beta * beta) ** 2);
  let u = 0, v = 0, w = 0, th = 0.35, x = 0, y = 0, t = 0;
  const T: number[] = [], X: number[] = [], Y: number[] = [], TH: number[] = [];
  while (t < 34) {
    const sp = Math.hypot(u, v) + 1e-9;
    const G = -CT * u * v / sp + CR * w;
    const F = (A - B * (u * u - v * v) / (sp * sp)) * sp / Math.PI;
    const du = (mV * v * w - G * v - Math.sin(th) - F * u) / mU;
    const dv = (-mU * u * w + G * u - Math.cos(th) - F * v) / mV;
    const dw = ((mU - mV) * u * v - (mu1 + mu2 * Math.abs(w)) * w) / It;
    u += du * dt; v += dv * dt; w += dw * dt; th += w * dt;
    x += (u * Math.cos(th) - v * Math.sin(th)) * dt; y += (u * Math.sin(th) + v * Math.cos(th)) * dt; t += dt;
    T.push(t); X.push(x); Y.push(y); TH.push(th);
  }
  // start once the flutter is developed, at the end of a swing; map 1.25 s of it to a 150-unit drop
  const i0 = T.findIndex((tt, i) => tt > 15 && i > 0 && (X[i] - X[i - 1]) * (X[i + 1] - X[i]) < 0);
  const i1 = i0 + Math.round((FALL_S * ND_PER_S) / dt);
  const S = 150 / (Y[i0] - Y[i1]);
  let mn = Infinity, mx = -Infinity;
  for (let i = i0; i <= i1; i++) { const q = (X[i] - X[i0]) * S; mn = Math.min(mn, q); mx = Math.max(mx, q); }
  const mid = (mn + mx) / 2;
  const out: Sample[] = [];
  for (let k = 0; k <= FALL_S * HZ; k++) {
    const i = i0 + Math.round((k / HZ) * ND_PER_S / dt);
    out.push({ x: (X[i] - X[i0]) * S - mid, y: -185 + (Y[i0] - Y[i]) * S, spine: -TH[i] * DEG, s: 1, phi: 0 });
  }
  // 2. the spiral: dragged by air swirling into the screen
  const INFLOW = 25, SWIRL = 22, DRAG = 60, VMAX = 600;
  const a = out[out.length - 2], b = out[out.length - 1];
  let px = b.x, py = b.y, vx = (b.x - a.x) * HZ, vy = (b.y - a.y) * HZ, spine = b.spine, tt = 0, merge: number | null = null;
  const r0 = Math.hypot(px - SCREEN.x, py - SCREEN.y);
  while (tt < 0.6) {
    const ramp = Math.min(1, tt / 0.06), dx = px - SCREEN.x, dy = py - SCREEN.y;
    let ax = -SWIRL * dy - INFLOW * dx, ay = SWIRL * dx - INFLOW * dy;
    const sp = Math.hypot(ax, ay); if (sp > VMAX) { ax *= VMAX / sp; ay *= VMAX / sp; }
    vx += ramp * DRAG * (ax - vx) / HZ; vy += ramp * DRAG * (ay - vy) / HZ;
    px += vx / HZ; py += vy / HZ; tt += 1 / HZ;
    let want = Math.atan2(vy, vx) * DEG;
    while (want - spine > 180) want -= 360;
    while (want - spine < -180) want += 360;
    spine += (want - spine) * 0.024;
    const r = Math.hypot(px - SCREEN.x, py - SCREEN.y);
    out.push({ x: px, y: py, spine, s: 0.28 + 0.72 * Math.pow(Math.min(1, r / r0), 0.85), phi: 0 });
    if (r < 3) { merge = FALL_S + tt; break; }
  }
  // 3. the twirl, from the airspeed along the path
  let om = 0, phi = 0.4;
  out.forEach((p, k) => {
    const q = out[Math.max(0, k - 1)], speed = Math.hypot(p.x - q.x, p.y - q.y) * HZ;
    om += (SPIN_PER_SPEED * speed - om) / (SPIN_LAG * HZ);
    phi += om / HZ; p.phi = phi;
  });
  return { out, merge: merge ?? FALL_S + 0.36 };
}

const jitter = (frame: number, salt: number) => { const s = Math.sin(frame * 12.9898 + salt * 78.233) * 43758.5453; return s - Math.floor(s) - 0.5; };
const COLORS = ["#ffd2b0", "#fff8f2", "#ff9a4a", "#ff6a8f", "#ffc37a"];

export function LaunchAnimation({ onDone }: { onDone: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const [fading, setFading] = useState(false);

  useEffect(() => {
    const root = host.current!;
    root.innerHTML = SCENE_SVG;            // static markup from launchScene.ts
    const svg = root.querySelector("svg")!;
    const $ = (id: string) => svg.querySelector<SVGElement>("#" + id)!;
    const set = (el: Element, attrs: Record<string, string | number>) => {
      for (const k in attrs) { const v = attrs[k]; el.setAttribute(k, typeof v === "number" ? String(+v.toFixed(3)) : v); }
    };
    svg.setAttribute("aria-label", "Featherbox is starting");

    const { out, merge: MERGE } = simulate();
    const DUR = MERGE + 0.45;
    const featherAt = (t: number) => {
      const k = clamp(t * HZ, 0, out.length - 1), i = Math.floor(k), f = k - i;
      const a = out[i], b = out[Math.min(i + 1, out.length - 1)];
      return { x: lerp(a.x, b.x, f), y: lerp(a.y, b.y, f), spine: lerp(a.spine, b.spine, f), s: lerp(a.s, b.s, f), phi: lerp(a.phi, b.phi, f) };
    };

    const NS = "http://www.w3.org/2000/svg";
    const blobs = Array.from({ length: 11 }, (_, i) => {
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("fill", COLORS[i % COLORS.length]); c.setAttribute("filter", "url(#claySmall)");
      $("blobs").appendChild(c);
      return { c, a: (i / 11) * Math.PI * 2 + 0.35, d: 66 + (i % 3) * 14, r: 2.6 + (i % 3) * 1.1, lag: (i % 4) * 0.025 };
    });
    const boils = svg.querySelectorAll(".boil");

    // the artwork keeps a fixed real size; the backdrop fills the window
    const layout = () => {
      const w = window.innerWidth, h = window.innerHeight;
      svg.setAttribute("viewBox", `${-w / 2} ${-h / 2} ${w} ${h}`);
      for (const r of svg.querySelectorAll(".bgfill")) set(r, { x: -w / 2, y: -h / 2, width: w, height: h });
      $("art").setAttribute("transform", `scale(${(LAPTOP_PX / LAPTOP_UNITS).toFixed(4)})`);
    };
    layout();
    window.addEventListener("resize", layout);

    const draw = (frameNo: number) => {
      const t = Math.min(DUR, frameNo / FPS);
      for (const n of boils) n.setAttribute("seed", String(1 + (frameNo % 6)));   // the clay boils
      const jx = jitter(frameNo, 1) * 0.9, jy = jitter(frameNo, 2) * 0.9;

      const fe = featherAt(t);
      const alive = smooth(seg(t, MERGE, MERGE + 0.34));
      const up = spring(seg(t, MERGE, DUR), 5, 9);
      const impact = t > MERGE && t < MERGE + 0.22 ? Math.sin(seg(t, MERGE, MERGE + 0.22) * Math.PI) : 0;

      const fo = smooth(clamp(t / 0.25)) * (1 - smooth(seg(t, MERGE - 0.03, MERGE + 0.02)));
      const tw = Math.cos(fe.phi);
      const width = Math.sign(tw || 1) * Math.max(0.14, Math.abs(tw));
      const cork = Math.sin(fe.phi) * 5 * (1 - seg(t, FALL_S, MERGE));
      set($("featherG"), { transform: `translate(${(fe.x + cork + jx).toFixed(2)} ${(fe.y + jy).toFixed(2)}) rotate(${fe.spine.toFixed(2)}) scale(${fe.s.toFixed(3)})`, opacity: fo });
      set($("featherSpin"), { transform: `scale(1 ${width.toFixed(3)})` });

      const sigh = Math.sin(t * 2.4) * (1 - alive);
      const hope = smooth(seg(t, 0.9, 1.3)) * (1 - alive);
      const lidTilt = lerp(-5, 0, up) + sigh * 0.5 + hope * 2;
      set($("lid"), { transform: `rotate(${lidTilt.toFixed(2)} 0 52) translate(0 ${lerp(3.5, 0, up).toFixed(2)}) scale(1 ${lerp(0.9 + 0.04 * hope, 1, up).toFixed(3)})` });
      const grow = lerp(1, 1.12, up);
      set($("laptop"), { transform: `translate(${(jx * 0.5).toFixed(2)} ${(lerp(0, -5, up) + sigh * 0.5 + jy * 0.5).toFixed(2)}) translate(0 66) scale(${(grow * (1 + 0.06 * impact)).toFixed(3)} ${(grow * (1 - 0.07 * impact)).toFixed(3)}) translate(0 -66)` });
      set($("lidP"), { fill: mix("a7a6ae", "3d3448", alive) });
      set($("baseP"), { fill: mix("b2b1b8", "4b4157", alive) });
      set($("padP"), { fill: mix("9b9aa2", "3a3245", alive) });
      set($("screenP"), { fill: mix("6f7079", "231a27", alive) });
      set($("rimP"), { opacity: alive });
      set($("crack"), { opacity: 1 - smooth(seg(t, MERGE, MERGE + 0.18)) });

      const lookX = clamp(fe.x / 110, -1, 1) * 2.4, lookY = clamp((fe.y - 20) / 140, -1, 0.3) * 2.2;
      set($("eyeL"), { cx: -14 + lookX, cy: 18 + lookY, ry: lerp(3.4, 4.4, hope) });
      set($("eyeR"), { cx: 14 + lookX, cy: 18 + lookY, ry: lerp(3.4, 4.4, hope) });
      const lidY = lerp(15.5, 12, hope);
      set($("lidL"), { d: `M-19,${lidY} Q-14,${lidY - 2.5} -9,${lidY}` });
      set($("lidR"), { d: `M19,${lidY} Q14,${lidY - 2.5} 9,${lidY}` });
      set($("browL"), { d: `M-21,${9 - hope} L-10,${lerp(6, 3.6, hope)}` });
      set($("browR"), { d: `M21,${9 - hope} L10,${lerp(6, 3.6, hope)}` });
      set($("mouth"), { d: `M-9,34 Q0,${lerp(28, 33, hope)} 9,34` });
      const tp = (t % 1.4) / 1.4;
      set($("tear"), { cy: 25 + easeOut(tp) * 11, opacity: Math.sin(tp * Math.PI) * (1 - hope) });
      set($("face"), { opacity: 1 - smooth(seg(t, MERGE - 0.05, MERGE + 0.06)) });

      const fl = seg(t, MERGE, MERGE + 0.2);
      set($("screenFlash"), { opacity: fl > 0 && fl < 1 ? 0.9 * Math.sin(fl * Math.PI) : 0 });
      const rg = seg(t, MERGE, MERGE + 0.45);
      set($("ring"), { r: lerp(6, 130, easeOut(rg)), opacity: rg > 0 && rg < 1 ? (1 - rg) * 0.7 : 0, "stroke-width": lerp(4, 0.6, rg) });

      const lg = spring(seg(t, MERGE + 0.02, DUR), 5.5, 10);
      set($("logoOn"), { transform: `translate(${SCREEN.x} ${SCREEN.y})`, opacity: smooth(seg(t, MERGE, MERGE + 0.08)) });
      set($("logoScale"), { transform: `scale(${(1.08 * lg).toFixed(3)}) rotate(${lerp(-25, 0, easeOut(seg(t, MERGE, MERGE + 0.4))).toFixed(2)})` });

      set($("auraC"), { opacity: alive * (0.8 + 0.2 * Math.sin(t * 7)) });
      blobs.forEach(({ c, a, d, r, lag }, i) => {
        const p = seg(t, MERGE + 0.04 + lag, DUR), k = spring(p, 4.5, 8), ang = a + p * 0.4;
        set(c, { cx: Math.cos(ang) * d * k + jitter(frameNo, 10 + i) * 0.8, cy: 22 + Math.sin(ang) * d * 0.75 * k, r: r * Math.min(1, k * 1.2), opacity: p > 0 ? 1 : 0 });
      });
      set($("shadow"), { rx: lerp(64, 74, up) - 4 * impact, opacity: lerp(0.4, 0.32, up) });

      const wd = easeOut(seg(t, MERGE + 0.12, DUR));
      set($("word"), { opacity: wd, transform: `translate(0 ${(8 * (1 - wd)).toFixed(2)})` });
    };

    const lastFrame = Math.ceil(DUR * FPS);
    // The clock only runs while the window is visible: a window that opens behind others (Edge then reports
    // it as hidden) or gets minimised waits, and the animation plays from where it was once you see it.
    let raf = 0, last = 0, elapsed = 0, shown = -1, finished = false, timer = 0, safety = 0;
    const finish = (holdMs: number) => {
      if (finished) return;
      finished = true;
      cancelAnimationFrame(raf); clearTimeout(safety);
      draw(lastFrame);                                   // always end on the finished picture
      timer = window.setTimeout(() => {
        setFading(true);
        timer = window.setTimeout(onDone, FADE_MS);
      }, holdMs);
    };
    const tick = (now: number) => {
      if (last) elapsed += Math.min(0.1, (now - last) / 1000);   // a long gap (window was hidden) doesn't skip ahead
      last = now;
      const frameNo = Math.floor(elapsed * FPS);
      if (frameNo !== shown) { shown = frameNo; draw(Math.min(frameNo, lastFrame)); }   // only 12 redraws a second
      if (frameNo >= lastFrame) finish(HOLD_S * 1000);
      else raf = requestAnimationFrame(tick);
    };
    // safety net: never cover a visible window for more than 4 s, whatever happens to the frame loop
    const arm = () => { clearTimeout(safety); safety = window.setTimeout(() => finish(0), 4000); };
    const onVisibility = () => {
      if (finished) return;
      if (document.hidden) { clearTimeout(safety); cancelAnimationFrame(raf); last = 0; }
      else { arm(); raf = requestAnimationFrame(tick); }
    };

    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    draw(0);
    if (reduced) finish(500);
    else if (!document.hidden) { arm(); raf = requestAnimationFrame(tick); }

    const skip = () => finish(0);
    window.addEventListener("keydown", skip);
    root.addEventListener("pointerdown", skip);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelAnimationFrame(raf); clearTimeout(timer); clearTimeout(safety);
      window.removeEventListener("resize", layout);
      window.removeEventListener("keydown", skip);
      root.removeEventListener("pointerdown", skip);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return <div ref={host} className={`launch ${fading ? "fading" : ""}`} aria-live="polite" />;
}

/** Play once per app window (a page reload in the same window doesn't replay it). */
export function shouldPlayLaunch(): boolean {
  try {
    if (sessionStorage.getItem("featherbox.launched")) return false;
    sessionStorage.setItem("featherbox.launched", "1");
    return true;
  } catch {
    return true;
  }
}
