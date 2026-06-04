import React from "react";
import { STRATA } from "../data/mock.js";
/* ============================================================
   strata — chart system (Charts)
   SVG, measured width, the palette. Distinct languages per type.
   ============================================================ */
  const { useRef, useState, useLayoutEffect, useMemo } = React;

  function useMeasure() {
    const ref = useRef(null);
    const [w, setW] = useState(0);
    useLayoutEffect(() => {
      if (!ref.current) return;
      const ro = new ResizeObserver(es => setW(es[0].contentRect.width));
      ro.observe(ref.current);
      setW(ref.current.clientWidth);
      return () => ro.disconnect();
    }, []);
    return [ref, w];
  }

  // catmull-rom -> smooth path
  function smoothPath(pts) {
    if (pts.length < 2) return "";
    let d = `M ${pts[0][0]},${pts[0][1]}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
      const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
      d += ` C ${c1x},${c1y} ${c2x},${c2y} ${p2[0]},${p2[1]}`;
    }
    return d;
  }
  const linePath = pts => pts.map((p, i) => (i ? "L" : "M") + p[0] + "," + p[1]).join(" ");

  // ---------- Salary trend: smooth area + line ----------
  function SalaryTrend({ series, code, height = 200 }) {
    const [ref, w] = useMeasure();
    const [hi, setHi] = useState(null);
    const padL = 8, padR = 8, padT = 16, padB = 26;
    const vals = series.map(s => s.value);
    const min = Math.min(...vals) * 0.96, max = Math.max(...vals) * 1.04;
    const iw = Math.max(10, w - padL - padR), ih = height - padT - padB;
    const x = i => padL + (i / (series.length - 1)) * iw;
    const y = v => padT + (1 - (v - min) / (max - min)) * ih;
    const pts = series.map((s, i) => [x(i), y(s.value)]);
    const d = smoothPath(pts);
    const area = d + ` L ${x(series.length - 1)},${padT + ih} L ${x(0)},${padT + ih} Z`;
    const fmt = STRATA.fmtCompact;
    return (
      <div ref={ref} style={{ position: "relative", width: "100%" }}>
        {w > 0 && (
          <svg width="100%" height={height} style={{ display: "block", overflow: "visible" }}>
            <defs>
              <linearGradient id={"sal" + code} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#2a5bff" stopOpacity="0.34" />
                <stop offset="100%" stopColor="#2a5bff" stopOpacity="0" />
              </linearGradient>
            </defs>
            {[0, 0.5, 1].map(g => (
              <line key={g} x1={padL} x2={padL + iw} y1={padT + g * ih} y2={padT + g * ih}
                stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
            ))}
            <path d={area} fill={"url(#sal" + code + ")"} />
            <path d={d} fill="none" stroke="#4a7cff" strokeWidth="2.5"
              strokeLinecap="round" style={{ filter: "drop-shadow(0 0 6px rgba(74,124,255,0.5))" }} />
            {series.map((s, i) => (
              <g key={i}>
                <circle cx={x(i)} cy={y(s.value)} r={hi === i ? 4.5 : 0} fill="#fff" />
                <rect x={x(i) - iw / series.length / 2} y={padT} width={iw / series.length} height={ih}
                  fill="transparent" onMouseEnter={() => setHi(i)} onMouseLeave={() => setHi(null)} />
              </g>
            ))}
            {series.filter((_, i) => i % 2 === 0).map((s, idx) => {
              const i = idx * 2;
              return <text key={i} x={x(i)} y={height - 6} fill="rgba(255,255,255,0.34)"
                fontSize="10.5" textAnchor="middle" fontFamily="var(--font)">{s.year}</text>;
            })}
          </svg>
        )}
        {hi != null && (
          <div style={{
            position: "absolute", left: `${(hi / (series.length - 1)) * 100}%`, top: -6,
            transform: "translateX(-50%)", background: "rgba(16,18,26,0.96)", border: "1px solid var(--glass-line)",
            borderRadius: 9, padding: "6px 10px", fontSize: 12, fontWeight: 700, color: "#fff",
            pointerEvents: "none", whiteSpace: "nowrap", boxShadow: "0 8px 24px rgba(0,0,0,0.5)"
          }}>
            <span style={{ color: "var(--t3)", fontWeight: 600, marginRight: 6 }}>{series[hi].year}</span>
            {STRATA.fmtCur(series[hi].value, code)}
          </div>
        )}
      </div>
    );
  }

  // ---------- Demand trend: stepped/column area, sky-tinted, distinct ----------
  function DemandTrend({ series, height = 160, accent = "#7aa0ff" }) {
    const [ref, w] = useMeasure();
    const [hi, setHi] = useState(null);
    const padL = 6, padR = 6, padT = 12, padB = 22;
    const iw = Math.max(10, w - padL - padR), ih = height - padT - padB;
    const n = series.length;
    const bw = (iw / n) * 0.56;
    const y = v => padT + (1 - v / 100) * ih;
    return (
      <div ref={ref} style={{ position: "relative", width: "100%" }}>
        {w > 0 && (
          <svg width="100%" height={height} style={{ display: "block", overflow: "visible" }}>
            <defs>
              <linearGradient id="demGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={accent} stopOpacity="0.85" />
                <stop offset="100%" stopColor={accent} stopOpacity="0.18" />
              </linearGradient>
            </defs>
            {series.map((s, i) => {
              const cx = padL + (i + 0.5) * (iw / n);
              const yy = y(s.value);
              return (
                <g key={i} onMouseEnter={() => setHi(i)} onMouseLeave={() => setHi(null)} style={{ cursor: "default" }}>
                  <rect x={cx - (iw / n) / 2} y={padT} width={iw / n} height={ih} fill="transparent" />
                  <rect x={cx - bw / 2} y={yy} width={bw} height={padT + ih - yy} rx={bw / 2.4}
                    fill="url(#demGrad)" opacity={hi == null || hi === i ? 1 : 0.4}
                    style={{ transition: "opacity 0.2s" }} />
                </g>
              );
            })}
            {series.filter((_, i) => i % 2 === 0).map((s, idx) => {
              const i = idx * 2, cx = padL + (i + 0.5) * (iw / n);
              return <text key={i} x={cx} y={height - 5} fill="rgba(255,255,255,0.3)" fontSize="10"
                textAnchor="middle">{("" + s.year).slice(2)}</text>;
            })}
          </svg>
        )}
        {hi != null && (
          <div style={{
            position: "absolute", left: `${((hi + 0.5) / series.length) * 100}%`, top: -4,
            transform: "translateX(-50%)", background: "rgba(16,18,26,0.96)", border: "1px solid var(--glass-line)",
            borderRadius: 8, padding: "4px 9px", fontSize: 11.5, fontWeight: 700, color: "#fff",
            pointerEvents: "none", whiteSpace: "nowrap"
          }}>{series[hi].year} · {series[hi].value} demand</div>
        )}
      </div>
    );
  }

  // ---------- Forecast: history solid + projected dashed + confidence band ----------
  function ForecastChart({ history, forecast, height = 220 }) {
    const [ref, w] = useMeasure();
    const padL = 8, padR = 40, padT = 16, padB = 26;
    const all = [...history.map(h => h.value), ...forecast.map(f => f.hi), ...forecast.map(f => f.lo)];
    const min = Math.max(0, Math.min(...all) - 8), max = Math.min(100, Math.max(...all) + 6);
    const total = history.length + forecast.length;
    const iw = Math.max(10, w - padL - padR), ih = height - padT - padB;
    const x = idx => padL + (idx / (total - 1)) * iw;
    const y = v => padT + (1 - (v - min) / (max - min)) * ih;
    const hPts = history.map((h, i) => [x(i), y(h.value)]);
    const last = hPts[hPts.length - 1];
    const fPts = forecast.map((f, i) => [x(history.length + i), y(f.value)]);
    const bandTop = [last, ...forecast.map((f, i) => [x(history.length + i), y(f.hi)])];
    const bandBot = [last, ...forecast.map((f, i) => [x(history.length + i), y(f.lo)])];
    const bandD = linePath(bandTop) + " " + bandBot.slice().reverse().map(p => "L" + p[0] + "," + p[1]).join(" ") + " Z";
    return (
      <div ref={ref} style={{ width: "100%" }}>
        {w > 0 && (
          <svg width="100%" height={height} style={{ display: "block", overflow: "visible" }}>
            {[0, 0.5, 1].map(g => (
              <line key={g} x1={padL} x2={padL + iw} y1={padT + g * ih} y2={padT + g * ih}
                stroke="rgba(255,255,255,0.06)" />
            ))}
            <line x1={last[0]} x2={last[0]} y1={padT} y2={padT + ih} stroke="rgba(255,255,255,0.16)" strokeDasharray="3 4" />
            <path d={bandD} fill="rgba(74,124,255,0.16)" />
            <path d={smoothPath(hPts)} fill="none" stroke="#4a7cff" strokeWidth="2.5" strokeLinecap="round" />
            <path d={linePath([last, ...fPts])} fill="none" stroke="#7aa0ff" strokeWidth="2.5"
              strokeDasharray="5 5" strokeLinecap="round" />
            {fPts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r="3" fill="#bcd2ff" />)}
            <text x={last[0] + 6} y={padT + 11} fill="rgba(255,255,255,0.4)" fontSize="9.5" letterSpacing="0.1em">PROJECTED</text>
            {[...history, ...forecast].filter((_, i) => i % 2 === 0).map((s, idx) => {
              const i = idx * 2;
              return <text key={i} x={x(i)} y={height - 6} fill="rgba(255,255,255,0.32)" fontSize="10"
                textAnchor="middle">{("" + s.year).slice(2)}</text>;
            })}
          </svg>
        )}
      </div>
    );
  }

  // ---------- Horizontal ranked bars ----------
  // each item: { label, value, geom?, ... }. geom (if present) drives bar
  // WIDTH so cross-currency comparisons stay fair; value drives the label.
  function RankBars({ items, max, fmt, height = 26, accentFn, onItemClick }) {
    const g = it => (it.geom != null ? it.geom : it.value);
    const mx = max || Math.max(...items.map(g));
    return (
      <div className="col" style={{ gap: 11 }}>
        {items.map((it, i) => {
          const clickable = !!onItemClick;
          return (
          <div key={i} className="row gap12" style={{ alignItems: "center", cursor: clickable ? "pointer" : "default", borderRadius: 8 }}
            onClick={clickable ? (e) => onItemClick(it, e) : undefined}
            onMouseEnter={clickable ? e => e.currentTarget.style.background = "rgba(255,255,255,0.025)" : undefined}
            onMouseLeave={clickable ? e => e.currentTarget.style.background = "transparent" : undefined}>
            <div style={{ width: 132, fontSize: 13, color: "var(--t1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", padding: clickable ? "2px 0 2px 6px" : 0 }}>{it.label}</div>
            <div style={{ flex: 1, height, background: "rgba(255,255,255,0.05)", borderRadius: 7, overflow: "hidden", position: "relative" }}>
              <div style={{
                width: `${(g(it) / mx) * 100}%`, height: "100%", borderRadius: 7,
                background: accentFn ? accentFn(it) : "linear-gradient(90deg,#0033ff,#4a7cff)",
                transition: "width 0.7s cubic-bezier(0.2,0.7,0.2,1)", minWidth: 4,
                boxShadow: "0 0 14px rgba(74,124,255,0.4)"
              }} />
            </div>
            <div className="tnum row gap6" style={{ width: clickable ? 92 : 78, justifyContent: "flex-end", alignItems: "center", fontSize: 13, fontWeight: 700, color: "#fff" }}>
              {fmt ? fmt(it.value) : it.value}
              {clickable && <span style={{ color: "var(--t3)", fontSize: 11 }}>›</span>}
            </div>
          </div>
        );})}
      </div>
    );
  }

  // ---------- Radar (resume vs market) ----------
  function Radar({ axes, size = 280, labels = ["You", "Market"] }) {
    const cx = size / 2, cy = size / 2, R = size / 2 - 44;
    const n = axes.length;
    const ang = i => -Math.PI / 2 + (i / n) * Math.PI * 2;
    const pt = (i, v) => [cx + Math.cos(ang(i)) * R * (v / 100), cy + Math.sin(ang(i)) * R * (v / 100)];
    const poly = key => axes.map((a, i) => pt(i, a[key]).join(",")).join(" ");
    return (
      <svg width={size} height={size} style={{ display: "block", overflow: "visible" }}>
        {[0.25, 0.5, 0.75, 1].map(r => (
          <polygon key={r} points={axes.map((_, i) => [cx + Math.cos(ang(i)) * R * r, cy + Math.sin(ang(i)) * R * r].join(",")).join(" ")}
            fill="none" stroke="rgba(255,255,255,0.08)" />
        ))}
        {axes.map((_, i) => <line key={i} x1={cx} y1={cy} x2={cx + Math.cos(ang(i)) * R} y2={cy + Math.sin(ang(i)) * R} stroke="rgba(255,255,255,0.07)" />)}
        <polygon points={poly("market")} fill="rgba(255,255,255,0.05)" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5" strokeDasharray="4 4" />
        <polygon points={poly("you")} fill="rgba(42,91,255,0.22)" stroke="#4a7cff" strokeWidth="2"
          style={{ filter: "drop-shadow(0 0 8px rgba(74,124,255,0.5))" }} />
        {axes.map((a, i) => {
          const [lx, ly] = [cx + Math.cos(ang(i)) * (R + 22), cy + Math.sin(ang(i)) * (R + 22)];
          return <text key={i} x={lx} y={ly} fill="rgba(255,255,255,0.55)" fontSize="10.5" fontWeight="600"
            textAnchor={Math.abs(Math.cos(ang(i))) < 0.3 ? "middle" : Math.cos(ang(i)) > 0 ? "start" : "end"}
            dominantBaseline="middle">{a.axis}</text>;
        })}
      </svg>
    );
  }

  // ---------- Durability micro-bar ----------
  function Durability({ value, width = 64 }) {
    const color = value >= 75 ? "var(--good)" : value >= 55 ? "var(--warn)" : "var(--bad)";
    return (
      <div className="dura-track" style={{ width }}>
        <div className="dura-fill" style={{ width: `${value}%`, background: color, boxShadow: `0 0 8px ${color}` }} />
      </div>
    );
  }

  // ---------- Donut (pay transparency) ----------
  function Donut({ value, size = 96, label }) {
    const r = size / 2 - 8, circ = 2 * Math.PI * r;
    const color = value >= 0.6 ? "#4fd99b" : value >= 0.4 ? "#ffcc4d" : "#ff6b6b";
    return (
      <div style={{ position: "relative", width: size, height: size }}>
        <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="7" />
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="7" strokeLinecap="round"
            strokeDasharray={`${circ * value} ${circ}`} style={{ filter: `drop-shadow(0 0 6px ${color})`, transition: "stroke-dasharray 0.8s" }} />
        </svg>
        <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", flexDirection: "column" }}>
          <div className="tnum" style={{ fontSize: 20, fontWeight: 700, color: "#fff", lineHeight: 1 }}>{Math.round(value * 100)}%</div>
        </div>
      </div>
    );
  }

  // ---------- Connected dual-line (convergence/divergence, role vs role) ----------
  function DualLine({ a, b, height = 200, labelA, labelB, fmtFn, code }) {
    const [ref, w] = useMeasure();
    const padL = 8, padR = 8, padT = 16, padB = 26;
    const allV = [...a.map(p => p.value), ...b.map(p => p.value)];
    const min = Math.min(...allV) * 0.95, max = Math.max(...allV) * 1.05;
    const iw = Math.max(10, w - padL - padR), ih = height - padT - padB;
    const x = i => padL + (i / (a.length - 1)) * iw;
    const y = v => padT + (1 - (v - min) / (max - min)) * ih;
    const pa = a.map((p, i) => [x(i), y(p.value)]);
    const pb = b.map((p, i) => [x(i), y(p.value)]);
    return (
      <div ref={ref} style={{ width: "100%" }}>
        {w > 0 && (
          <svg width="100%" height={height} style={{ display: "block", overflow: "visible" }}>
            {[0, 0.5, 1].map(g => <line key={g} x1={padL} x2={padL + iw} y1={padT + g * ih} y2={padT + g * ih} stroke="rgba(255,255,255,0.06)" />)}
            <path d={smoothPath(pa)} fill="none" stroke="#4a7cff" strokeWidth="2.5" strokeLinecap="round" />
            <path d={smoothPath(pb)} fill="none" stroke="#ffcc4d" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="1 0" opacity="0.92" />
            {a.filter((_, i) => i % 2 === 0).map((s, idx) => {
              const i = idx * 2;
              return <text key={i} x={x(i)} y={height - 6} fill="rgba(255,255,255,0.32)" fontSize="10" textAnchor="middle">{("" + s.year).slice(2)}</text>;
            })}
          </svg>
        )}
      </div>
    );
  }

  // ---------- Radar (multi-series overlay; resume A/B, role shapes) ----------
  function RadarMulti({ axes, series, size = 300 }) {
    const cx = size / 2, cy = size / 2, R = size / 2 - 48;
    const n = axes.length;
    const ang = i => -Math.PI / 2 + (i / n) * Math.PI * 2;
    const poly = vals => vals.map((v, i) => [cx + Math.cos(ang(i)) * R * (Math.max(0, Math.min(100, v)) / 100), cy + Math.sin(ang(i)) * R * (Math.max(0, Math.min(100, v)) / 100)].join(",")).join(" ");
    return (
      <svg width={size} height={size} style={{ display: "block", overflow: "visible" }}>
        {[0.25, 0.5, 0.75, 1].map(r => (
          <polygon key={r} points={axes.map((_, i) => [cx + Math.cos(ang(i)) * R * r, cy + Math.sin(ang(i)) * R * r].join(",")).join(" ")}
            fill="none" stroke="rgba(255,255,255,0.08)" />
        ))}
        {axes.map((_, i) => <line key={i} x1={cx} y1={cy} x2={cx + Math.cos(ang(i)) * R} y2={cy + Math.sin(ang(i)) * R} stroke="rgba(255,255,255,0.07)" />)}
        {series.map((s, si) => (
          <polygon key={si} points={poly(s.values)} fill={s.color + "22"} stroke={s.color} strokeWidth="2"
            style={{ filter: `drop-shadow(0 0 6px ${s.color}aa)` }} />
        ))}
        {axes.map((a, i) => {
          const [lx, ly] = [cx + Math.cos(ang(i)) * (R + 24), cy + Math.sin(ang(i)) * (R + 24)];
          return <text key={i} x={lx} y={ly} fill="rgba(255,255,255,0.55)" fontSize="10.5" fontWeight="600"
            textAnchor={Math.abs(Math.cos(ang(i))) < 0.3 ? "middle" : Math.cos(ang(i)) > 0 ? "start" : "end"} dominantBaseline="middle">{a}</text>;
        })}
      </svg>
    );
  }

  // ---------- Multi-line overlay (N role/country trajectories) ----------
  function MultiLine({ series, height = 200, normalize = false, fmtFn, dotsLast = true }) {
    const [ref, w] = useMeasure();
    const [hi, setHi] = useState(null);
    const padL = 8, padR = 12, padT = 16, padB = 26;
    const norm = arr => { if (!normalize) return arr; const mx = Math.max(...arr.map(p => p.value)); return arr.map(p => ({ year: p.year, value: (p.value / mx) * 100 })); };
    const data = series.map(s => ({ ...s, points: norm(s.points) }));
    const allV = data.flatMap(s => s.points.map(p => p.value));
    const min = Math.min(...allV) * 0.95, max = Math.max(...allV) * 1.05;
    const len = data[0].points.length;
    const iw = Math.max(10, w - padL - padR), ih = height - padT - padB;
    const x = i => padL + (i / (len - 1)) * iw;
    const y = v => padT + (1 - (v - min) / (max - min)) * ih;
    return (
      <div ref={ref} style={{ position: "relative", width: "100%" }}>
        {w > 0 && (
          <svg width="100%" height={height} style={{ display: "block", overflow: "visible" }}
            onMouseMove={e => { const rb = e.currentTarget.getBoundingClientRect(); const rel = e.clientX - rb.left - padL; setHi(Math.max(0, Math.min(len - 1, Math.round((rel / iw) * (len - 1))))); }}
            onMouseLeave={() => setHi(null)}>
            {[0, 0.5, 1].map(g => <line key={g} x1={padL} x2={padL + iw} y1={padT + g * ih} y2={padT + g * ih} stroke="rgba(255,255,255,0.06)" />)}
            {hi != null && <line x1={x(hi)} x2={x(hi)} y1={padT} y2={padT + ih} stroke="rgba(255,255,255,0.14)" />}
            {data.map((s, si) => {
              const pts = s.points.map((p, i) => [x(i), y(p.value)]);
              return <g key={si}>
                <path d={smoothPath(pts)} fill="none" stroke={s.color} strokeWidth="2.5" strokeLinecap="round" style={{ filter: `drop-shadow(0 0 5px ${s.color}88)` }} />
                {dotsLast && <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="3.5" fill={s.color} />}
                {hi != null && <circle cx={x(hi)} cy={y(s.points[hi].value)} r="3.5" fill="#fff" stroke={s.color} strokeWidth="2" />}
              </g>;
            })}
            {data[0].points.filter((_, i) => i % 2 === 0).map((p, idx) => {
              const i = idx * 2;
              return <text key={i} x={x(i)} y={height - 6} fill="rgba(255,255,255,0.32)" fontSize="10" textAnchor="middle">{("" + p.year).slice(2)}</text>;
            })}
          </svg>
        )}
        {hi != null && (
          <div style={{ position: "absolute", left: `${(hi / (len - 1)) * 100}%`, top: -8, transform: "translateX(-50%)", background: "rgba(16,18,26,0.97)", border: "1px solid var(--glass-line)", borderRadius: 9, padding: "7px 10px", pointerEvents: "none", whiteSpace: "nowrap", boxShadow: "0 8px 24px rgba(0,0,0,0.5)", zIndex: 5 }}>
            <div style={{ fontSize: 10.5, color: "var(--t3)", fontWeight: 600, marginBottom: 3 }}>{data[0].points[hi].year}</div>
            {data.map((s, si) => (
              <div key={si} className="row gap8" style={{ alignItems: "center", fontSize: 11.5 }}>
                <span style={{ width: 8, height: 8, borderRadius: 9, background: s.color, display: "inline-block" }}></span>
                <span style={{ color: "var(--t2)", marginRight: 6 }}>{s.label}</span>
                <span className="tnum" style={{ color: "#fff", fontWeight: 700, marginLeft: "auto" }}>{fmtFn ? fmtFn(s.points[hi].value, s) : Math.round(s.points[hi].value)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  const SERIES_COLORS = ["#4a7cff", "#ffcc4d", "#4fd99b", "#b08cff"];

  // ---------- Scatter / positioning map ----------
  function Scatter({ points, xLabel, yLabel, height = 240, quadrant }) {
    const [ref, w] = useMeasure();
    const padL = 38, padR = 16, padT = 16, padB = 34;
    const iw = Math.max(10, w - padL - padR), ih = height - padT - padB;
    const X = v => padL + (v / 100) * iw, Y = v => padT + (1 - v / 100) * ih;
    return (
      <div ref={ref} style={{ width: "100%" }}>
        {w > 0 && (
          <svg width="100%" height={height} style={{ display: "block", overflow: "visible" }}>
            {[0, 25, 50, 75, 100].map(g => <line key={"x" + g} x1={X(g)} x2={X(g)} y1={padT} y2={padT + ih} stroke="rgba(255,255,255,0.05)" />)}
            {[0, 25, 50, 75, 100].map(g => <line key={"y" + g} x1={padL} x2={padL + iw} y1={Y(g)} y2={Y(g)} stroke="rgba(255,255,255,0.05)" />)}
            {quadrant && <line x1={X(50)} x2={X(50)} y1={padT} y2={padT + ih} stroke="rgba(74,124,255,0.25)" strokeDasharray="3 4" />}
            {quadrant && <line x1={padL} x2={padL + iw} y1={Y(50)} y2={Y(50)} stroke="rgba(74,124,255,0.25)" strokeDasharray="3 4" />}
            {quadrant && <text x={padL + iw - 4} y={padT + 12} fill="rgba(79,217,155,0.7)" fontSize="9.5" textAnchor="end" letterSpacing="0.08em">HIGH OPPORTUNITY</text>}
            {points.map((p, i) => (
              <g key={i}>
                <circle cx={X(p.x)} cy={Y(p.y)} r="13" fill={p.color + "22"} />
                <circle cx={X(p.x)} cy={Y(p.y)} r="6" fill={p.color} stroke="#0a0b0e" strokeWidth="1.5" style={{ filter: `drop-shadow(0 0 6px ${p.color}cc)` }} />
                <text x={X(p.x)} y={Y(p.y) - 12} fill="rgba(255,255,255,0.8)" fontSize="10.5" fontWeight="600" textAnchor="middle">{p.label}</text>
              </g>
            ))}
            <text x={padL + iw / 2} y={height - 6} fill="rgba(255,255,255,0.4)" fontSize="10.5" textAnchor="middle">{xLabel} →</text>
            <text x={12} y={padT + ih / 2} fill="rgba(255,255,255,0.4)" fontSize="10.5" textAnchor="middle" transform={`rotate(-90 12 ${padT + ih / 2})`}>{yLabel} →</text>
          </svg>
        )}
      </div>
    );
  }

  export const Charts = { useMeasure, SalaryTrend, DemandTrend, ForecastChart, RankBars, Radar, RadarMulti, MultiLine, Scatter, Durability, Donut, DualLine, smoothPath, linePath, SERIES_COLORS };
