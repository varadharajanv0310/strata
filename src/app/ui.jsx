import React from "react";
import { STRATA } from "../data/mock.js";
import { Charts } from "./charts.jsx";

let LAND_CACHE = null;
/* ============================================================
   strata — shared UI primitives (UI)
   ============================================================ */
  const { useState, useEffect, useRef } = React;
  const S = () => STRATA;

  function useCountUp(target, dur = 850, deps = []) {
    const [v, setV] = useState(target);
    const prev = useRef(target);
    useEffect(() => {
      const from = prev.current, to = target;
      let raf, start;
      const ease = p => 1 - Math.pow(1 - p, 3);
      const step = t => {
        if (!start) start = t;
        const p = Math.min(1, (t - start) / dur);
        setV(from + (to - from) * ease(p));
        if (p < 1) raf = requestAnimationFrame(step); else prev.current = to;
      };
      raf = requestAnimationFrame(step);
      return () => cancelAnimationFrame(raf);
    }, [target, ...deps]);
    return v;
  }

  function Wordmark({ size = 19, onClick }) {
    return (
      <div className="brand" onClick={onClick}>
        <div className="logomark"><span></span><span></span><span></span></div>
        <div className="wordmark" style={{ fontSize: size }}>strata<sup>®</sup></div>
      </div>
    );
  }

  // ---- atmospheric globe (decorative) ----
  function Globe({ size = 340, grid = true }) {
    return (
      <div className="globe-wrap" style={{ width: size, height: size }}>
        <div className="globe-sphere" style={{ width: size, height: size }}>
          {grid && (
            <svg className="globe-grid" viewBox="0 0 100 100" preserveAspectRatio="none">
              <circle cx="50" cy="50" r="49" fill="none" stroke="rgba(255,255,255,0.18)" strokeWidth="0.3" />
              {[18, 32, 44].map(r => <ellipse key={r} cx="50" cy="50" rx={r} ry="49" fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="0.25" />)}
              {[20, 35, 47].map(r => <ellipse key={r} cx="50" cy="50" rx="49" ry={r} fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="0.25" />)}
              <line x1="1" y1="50" x2="99" y2="50" stroke="rgba(255,255,255,0.12)" strokeWidth="0.25" />
              <line x1="50" y1="1" x2="50" y2="99" stroke="rgba(255,255,255,0.12)" strokeWidth="0.25" />
            </svg>
          )}
        </div>
      </div>
    );
  }

  // ---- interactive dotted-world globe: continents as lit dots; 7 country nodes ----
  const GEO = { IN: [22, 78], US: [39, -98], GB: [54, -2], CA: [56, -106], AU: [-25, 133], SG: [1.3, 103.8], DE: [51, 10] };
  // rough continent outlines as [lon,lat] polygons — read as Earth once dotted
  const CONTINENTS = [
    [[-168,66],[-150,71],[-122,72],[-95,72],[-80,73],[-62,60],[-66,48],[-80,43],[-81,25],[-97,16],[-92,14],[-105,20],[-117,32],[-125,40],[-135,58],[-160,60],[-168,66]],
    [[-55,60],[-30,60],[-18,70],[-30,82],[-50,81],[-60,70],[-55,60]],
    [[-80,9],[-60,11],[-50,0],[-35,-5],[-38,-15],[-52,-32],[-66,-45],[-74,-52],[-72,-30],[-81,-10],[-80,9]],
    [[-10,36],[-10,44],[-4,49],[4,52],[10,58],[26,71],[42,67],[40,50],[28,45],[28,40],[16,40],[-2,36],[-10,36]],
    [[-8,50],[-6,58],[-2,59],[1,52],[-2,50],[-8,50]],
    [[-17,21],[-16,14],[-8,5],[8,4],[10,-18],[18,-35],[26,-34],[33,-26],[40,-16],[42,-3],[52,12],[44,12],[33,30],[10,37],[-6,36],[-12,28],[-17,21]],
    [[40,46],[28,42],[34,33],[36,29],[44,38],[50,28],[58,25],[60,40],[70,30],[68,24],[78,8],[80,14],[90,22],[97,8],[106,10],[110,21],[122,40],[130,34],[142,46],[160,62],[178,68],[150,72],[100,78],[60,72],[40,66],[40,46]],
    [[130,31],[136,34],[141,40],[145,44],[140,36],[135,33],[130,31]],
    [[95,6],[105,-6],[120,-8],[140,-8],[150,-9],[134,-2],[120,2],[100,8],[95,6]],
    [[113,-22],[122,-18],[131,-12],[142,-11],[150,-24],[146,-38],[138,-36],[129,-32],[115,-34],[113,-22]],
    [[166,-46],[174,-41],[178,-38],[172,-44],[166,-46]],
  ];
  function pip(lon, lat, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1];
      if (((yi > lat) !== (yj > lat)) && (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi)) inside = !inside;
    }
    return inside;
  }
  function buildLand() {
    const pts = [];
    for (let lat = -78; lat <= 82; lat += 1.7) for (let lon = -180; lon < 180; lon += 1.7) {
      for (let c = 0; c < CONTINENTS.length; c++) if (pip(lon, lat, CONTINENTS[c])) { pts.push([lat, lon]); break; }
    }
    return pts;
  }
  function rr(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }

  function InteractiveGlobe({ size = 440, active, onSelect }) {
    const canvasRef = useRef(null);
    const wrapRef = useRef(null);
    const rotRef = useRef(GEO[active] ? GEO[active][1] : 78);
    const targetRef = useRef(null);
    const dragRef = useRef(null);
    const hoverRef = useRef(null);
    const nodePosRef = useRef([]);
    const drawRef = useRef(null);
    const overRef = useRef(false);
    const activeRef = useRef(active);
    const lastActive = useRef(active);
    activeRef.current = active;

    useEffect(() => { if (active !== lastActive.current) { lastActive.current = active; if (GEO[active]) targetRef.current = GEO[active][1]; } }, [active]);

    useEffect(() => {
      const canvas = canvasRef.current;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = size * dpr; canvas.height = size * dpr;
      const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
      const R = size / 2 - 12, cx = size / 2, cy = size / 2, tilt = 16 * Math.PI / 180;
      const land = LAND_CACHE || (LAND_CACHE = buildLand());
      const Lm = Math.hypot(0.55, 0.4, 0.82), L = [0.55 / Lm, 0.4 / Lm, 0.82 / Lm];
      const t0 = performance.now();
      const draw = (advance) => {
        const now = performance.now(), time = (now - t0) / 1000;
        if (targetRef.current != null) { let d = ((targetRef.current - rotRef.current + 540) % 360) - 180; if (Math.abs(d) < 0.25) targetRef.current = null; else rotRef.current += d * 0.1; }
        else if (advance && !dragRef.current && !overRef.current) rotRef.current = (rotRef.current + 0.32) % 360;
        const rot = rotRef.current * Math.PI / 180, st = Math.sin(tilt), ct = Math.cos(tilt);
        ctx.clearRect(0, 0, size, size);
        const g = ctx.createRadialGradient(cx + R * 0.34, cy - R * 0.34, R * 0.08, cx, cy, R * 1.04);
        g.addColorStop(0, "#16356f"); g.addColorStop(0.5, "#0a1c40"); g.addColorStop(1, "#05080f");
        ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.fillStyle = g; ctx.fill();
        for (let i = 0; i < land.length; i++) {
          const la = land[i][0] * Math.PI / 180, lo = land[i][1] * Math.PI / 180 - rot;
          const cphi = Math.cos(la), sphi = Math.sin(la), clo = Math.cos(lo), slo = Math.sin(lo);
          const x = cphi * slo, y = ct * sphi - st * cphi * clo, z = st * sphi + ct * cphi * clo;
          if (z <= 0) continue;
          const bright = Math.max(0, x * L[0] + y * L[1] + z * L[2]);
          const a = 0.22 + bright * 0.78;
          const cr = Math.round(64 + bright * 178), cg = Math.round(118 + bright * 124), cb = Math.round(220 + bright * 35);
          const rad = (0.85 + bright * 1.15) * (0.55 + 0.45 * z);
          ctx.beginPath(); ctx.arc(cx + R * x, cy - R * y, rad, 0, 7); ctx.fillStyle = "rgba(" + cr + "," + cg + "," + cb + "," + a + ")"; ctx.fill();
        }
        ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.strokeStyle = "rgba(120,160,255,0.3)"; ctx.lineWidth = 1.1; ctx.stroke();
        const nodes = [];
        for (const co of S().COUNTRIES) {
          const [la0, lo0] = GEO[co.code]; const la = la0 * Math.PI / 180, lo = lo0 * Math.PI / 180 - rot;
          const cphi = Math.cos(la), sphi = Math.sin(la), clo = Math.cos(lo), slo = Math.sin(lo);
          const x = cphi * slo, y = ct * sphi - st * cphi * clo, z = st * sphi + ct * cphi * clo;
          const sx = cx + R * x, sy = cy - R * y, front = z > 0;
          nodes.push({ code: co.code, name: co.name, x: sx, y: sy, front });
          if (!front) continue;
          const isA = co.code === activeRef.current, isH = co.code === hoverRef.current;
          if (isA) { const pr = 11 + Math.sin(time * 2.4) * 4; ctx.beginPath(); ctx.arc(sx, sy, pr, 0, 7); ctx.strokeStyle = "rgba(74,124,255,0.55)"; ctx.lineWidth = 1.5; ctx.stroke(); }
          ctx.beginPath(); ctx.arc(sx, sy, (isA ? 8 : 5.5), 0, 7); ctx.fillStyle = "rgba(74,124,255,0.3)"; ctx.fill();
          ctx.beginPath(); ctx.arc(sx, sy, (isA ? 5 : isH ? 4.5 : 3.8), 0, 7); ctx.fillStyle = isA ? "#fff" : "#cfe0ff"; ctx.fill();
          ctx.lineWidth = isA ? 2.4 : 1.4; ctx.strokeStyle = "#0033ff"; ctx.stroke();
        }
        nodePosRef.current = nodes;
        ctx.font = '700 12px "Plus Jakarta Sans", system-ui, sans-serif';
        for (const n of nodes) {
          if (!n.front || (n.code !== activeRef.current && n.code !== hoverRef.current)) continue;
          const w = ctx.measureText(n.name).width + 18, leftSide = n.x > cx, lx = leftSide ? n.x - 12 - w : n.x + 12;
          ctx.fillStyle = "rgba(8,10,16,0.92)"; rr(ctx, lx, n.y - 12, w, 22, 11); ctx.fill();
          ctx.strokeStyle = "rgba(120,160,255,0.4)"; ctx.lineWidth = 1; rr(ctx, lx, n.y - 12, w, 22, 11); ctx.stroke();
          ctx.fillStyle = "#fff"; ctx.textBaseline = "middle"; ctx.textAlign = leftSide ? "right" : "left";
          ctx.fillText(n.name, leftSide ? lx + w - 9 : lx + 9, n.y + 1);
        }
      };
      drawRef.current = () => draw(false);
      draw(false); // immediate first paint
      // drive animation via rAF when available, with a setInterval fallback (rAF can be paused in some embeds)
      let raf, alive = true;
      const loop = () => { if (!alive) return; draw(true); raf = requestAnimationFrame(loop); };
      raf = requestAnimationFrame(loop);
      const iv = setInterval(() => draw(true), 60);
      return () => { alive = false; cancelAnimationFrame(raf); clearInterval(iv); };
    }, [size]);

    const onDown = e => { dragRef.current = { x: e.clientX, rot: rotRef.current, moved: false }; overRef.current = true; targetRef.current = null; try { e.currentTarget.setPointerCapture?.(e.pointerId); } catch (err) {} };
    const onMove = e => {
      overRef.current = true;
      const rect = canvasRef.current.getBoundingClientRect(), mx = e.clientX - rect.left, my = e.clientY - rect.top;
      if (dragRef.current) { const dx = e.clientX - dragRef.current.x; if (Math.abs(dx) > 3) dragRef.current.moved = true; rotRef.current = dragRef.current.rot - dx * 0.45; if (drawRef.current) drawRef.current(); }
      else { let h = null; for (const n of nodePosRef.current) if (n.front && Math.hypot(n.x - mx, n.y - my) < 15) { h = n.code; break; } hoverRef.current = h; if (wrapRef.current) wrapRef.current.style.cursor = h ? "pointer" : "grab"; }
    };
    const onUp = e => {
      const d = dragRef.current; dragRef.current = null;
      if (d && !d.moved) { const rect = canvasRef.current.getBoundingClientRect(), mx = e.clientX - rect.left, my = e.clientY - rect.top; for (const n of nodePosRef.current) if (n.front && Math.hypot(n.x - mx, n.y - my) < 17) { onSelect(n.code); break; } }
    };

    return (
      <div ref={wrapRef} className="globe-wrap" style={{ width: size, height: size, touchAction: "none", cursor: "grab" }}
        onPointerEnter={() => { overRef.current = true; }}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
        onPointerLeave={() => { dragRef.current = null; hoverRef.current = null; overRef.current = false; }}>
        <canvas ref={canvasRef} style={{ width: size, height: size, display: "block", position: "relative", zIndex: 1 }} />
      </div>
    );
  }

  // ---- country tag: 2-tone dot + label ----
  function CountryDot({ code, size = 12 }) {
    const c = S().C[code];
    return <span style={{
      width: size, height: size, borderRadius: 999, display: "inline-block", flexShrink: 0,
      background: `linear-gradient(135deg, ${c.c1} 0 50%, ${c.c2} 50% 100%)`,
      boxShadow: "0 0 0 1px rgba(255,255,255,0.18)"
    }} />;
  }
  function CountryTag({ code, showName = true, sm }) {
    const c = S().C[code];
    return (
      <span className="row gap8" style={{ alignItems: "center" }}>
        <CountryDot code={code} size={sm ? 10 : 13} />
        <span style={{ fontSize: sm ? 12 : 13, fontWeight: 600, color: "var(--t1)" }}>{showName ? c.name : code}</span>
      </span>
    );
  }

  // ---- country selector (pill dropdown) ----
  function CountrySelect({ value, onChange, compact }) {
    const [open, setOpen] = useState(false);
    const ref = useRef(null);
    useEffect(() => {
      const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
      document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
    }, []);
    const c = S().C[value];
    return (
      <div ref={ref} style={{ position: "relative" }}>
        <button className="pill" onClick={() => setOpen(o => !o)}>
          <CountryDot code={value} />
          <span>{compact ? value : c.name}</span>
          <span style={{ color: "var(--t3)", fontSize: 10 }}>▾</span>
        </button>
        {open && (
          <div style={{
            position: "absolute", top: "calc(100% + 8px)", left: 0, zIndex: 80, width: 210,
            background: "rgba(16,18,26,0.97)", border: "1px solid var(--glass-line)", borderRadius: 14,
            padding: 6, boxShadow: "0 24px 60px rgba(0,0,0,0.6)", backdropFilter: "blur(20px)", animation: "fadeUp 0.16s ease"
          }}>
            {S().COUNTRIES.map(co => (
              <button key={co.code} onClick={() => { onChange(co.code); setOpen(false); }}
                className="row gap10" style={{
                  width: "100%", padding: "9px 11px", borderRadius: 9, border: "none", cursor: "pointer",
                  background: co.code === value ? "rgba(42,91,255,0.2)" : "transparent",
                  color: "var(--t1)", fontFamily: "var(--font)", fontSize: 13.5, fontWeight: 600, textAlign: "left",
                  alignItems: "center"
                }}
                onMouseEnter={e => { if (co.code !== value) e.currentTarget.style.background = "rgba(255,255,255,0.05)"; }}
                onMouseLeave={e => { if (co.code !== value) e.currentTarget.style.background = "transparent"; }}>
                <CountryDot code={co.code} /> {co.name}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ---- confidence badge + provenance popover ----
  function ConfidenceBadge({ data, align = "left" }) {
    const [open, setOpen] = useState(false);
    const ref = useRef(null);
    useEffect(() => {
      const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
      document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
    }, []);
    const label = { high: "High confidence", med: "Medium", low: "Low confidence" }[data.conf];
    return (
      <span ref={ref} style={{ position: "relative", display: "inline-block" }}>
        <span className={"conf " + data.conf} onClick={() => setOpen(o => !o)}>
          <span className="dot" style={{ background: "currentColor" }}></span>{label}
        </span>
        {open && (
          <div className="prov-pop" style={{ top: "calc(100% + 8px)", [align]: 0 }}>
            <div className="eyebrow" style={{ marginBottom: 10 }}>Provenance</div>
            <div className="prov-row"><span className="k">Source</span><span className="v">{data.source}</span></div>
            <div className="prov-row"><span className="k">Sample size</span><span className="v tnum">{data.sample.toLocaleString()} postings</span></div>
            <div className="prov-row"><span className="k">Figure type</span><span className="v">{data.kind === "job-level" ? "Job-level (per posting)" : "Person-level"}</span></div>
            <div className="prov-row"><span className="k">Freshness</span><span className="v">Updated {data.freshness} ago</span></div>
            <div className="prov-row"><span className="k">Disclosure rate</span><span className="v tnum">{Math.round(data.transparency * 100)}% of postings</span></div>
            {(() => {
              const lin = (S().provenance || {})[data.source];
              if (!lin) return null;
              return (
                <>
                  <div className="eyebrow" style={{ margin: "12px 0 8px" }}>Lineage</div>
                  {lin.snapshotHash && <div className="prov-row"><span className="k">Snapshot</span><span className="v tnum">{lin.snapshotHash.slice(0, 12)}</span></div>}
                  {lin.transformVersion && <div className="prov-row"><span className="k">Transform</span><span className="v">{lin.transformVersion}</span></div>}
                  {lin.rowCount != null && <div className="prov-row"><span className="k">Rows</span><span className="v tnum">{lin.rowCount.toLocaleString()}</span></div>}
                  {lin.asOf && <div className="prov-row"><span className="k">As of</span><span className="v">{lin.asOf}</span></div>}
                </>
              );
            })()}
            <div className="small" style={{ marginTop: 10, color: "var(--t3)", lineHeight: 1.5 }}>
              {data.conf === "low" ? "Thin coverage — shown with low confidence rather than hidden." : "Figure derived from disclosed salary in real postings."}
            </div>
          </div>
        )}
      </span>
    );
  }

  // ---- big salary headline ----
  function BigSalary({ value, code, size = 44 }) {
    const hasVal = value != null && !Number.isNaN(value);
    const animated = useCountUp(hasVal ? value : 0, 900, [code]);
    if (!hasVal) {
      // a demand-only / derived role with no salary lens reads honestly, never ₹0
      return <span style={{ fontSize: Math.round(size * 0.42), fontWeight: 600, color: "var(--t3)", lineHeight: 1 }}>not enough data</span>;
    }
    return <span className="tnum" style={{ fontSize: size, fontWeight: 700, letterSpacing: "-0.03em", color: "#fff", lineHeight: 1 }}>
      {S().fmtCur(Math.round(animated), code)}
    </span>;
  }

  function ProfTag({ level }) {
    const map = { A: ["prof-adv", "Advanced"], I: ["prof-int", "Intermediate"], B: ["prof-beg", "Beginner"] };
    const [cls, txt] = map[level];
    return <span className={"prof-tag " + cls}>{txt}</span>;
  }

  // ---- skill row: name + level + durability ----
  function SkillRow({ skill }) {
    const trendIcon = { rising: "↗", stable: "→", fading: "↘" }[skill.trend];
    const trendColor = skill.trend === "rising" ? "var(--good)" : skill.trend === "fading" ? "var(--bad)" : "var(--t3)";
    return (
      <div className="row between gap12" style={{ padding: "11px 0", borderBottom: "1px solid var(--line)" }}>
        <div className="row gap10" style={{ minWidth: 0, flex: 1 }}>
          <span style={{ fontSize: 14, color: "var(--t1)", fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{skill.name}</span>
          <ProfTag level={skill.level} />
        </div>
        <div className="row gap10" style={{ alignItems: "center" }}>
          <span style={{ color: trendColor, fontSize: 13, fontWeight: 700, width: 14, textAlign: "center" }}>{trendIcon}</span>
          <Charts.Durability value={skill.dura} />
        </div>
      </div>
    );
  }

  // ---- score breakdown (expandable) ----
  function ScoreBreakdown({ score }) {
    const comps = [
      { k: "Demand", v: score.demand, hint: "How much the market wants this role" },
      { k: "Pay", v: score.pay, hint: "PPP-normalized median compensation" },
      { k: "Opportunity", v: score.opp, hint: "Demand outpacing learner interest" },
    ];
    return (
      <div className="score-break">
        {comps.map(c => (
          <div key={c.k} className="comp">
            <div className="row between"><span className="small" style={{ color: "var(--t2)" }}>{c.k}</span>
              <span className="tnum" style={{ fontSize: 13, fontWeight: 700, color: "#fff" }}>{c.v.toFixed(1)}</span></div>
            <div className="comp-bar"><span style={{ width: `${c.v * 10}%` }}></span></div>
            <div style={{ fontSize: 10.5, color: "var(--t3)", marginTop: 6, lineHeight: 1.4 }}>{c.hint}</div>
          </div>
        ))}
      </div>
    );
  }

  function FamilyDot({ family, size = 8 }) {
    return <span className="fam-dot" style={{ width: size, height: size, borderRadius: 999, display: "inline-block",
      background: `oklch(0.65 0.18 ${family.hue})`, boxShadow: `0 0 8px oklch(0.65 0.18 ${family.hue})` }} />;
  }

  function Empty({ icon, title, sub }) {
    return (
      <div className="col center" style={{ alignItems: "center", padding: "60px 20px", textAlign: "center", gap: 10 }}>
        <div style={{ fontSize: 30, opacity: 0.5 }}>{icon}</div>
        <div className="h3" style={{ color: "var(--t1)" }}>{title}</div>
        <div className="small" style={{ maxWidth: 360, color: "var(--t3)" }}>{sub}</div>
      </div>
    );
  }

  export const UI = { useCountUp, Wordmark, Globe, InteractiveGlobe, CountryDot, CountryTag, CountrySelect, ConfidenceBadge, BigSalary, ProfTag, SkillRow, ScoreBreakdown, FamilyDot, Empty };
