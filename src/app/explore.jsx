import React from "react";
import { STRATA } from "../data/mock.js";
import { UI } from "./ui.jsx";
import { Charts } from "./charts.jsx";
/* ============================================================
   strata — Explore surface (atmospheric landing + Market Pulse + free canvas)
   ============================================================ */
  const { useState, useEffect } = React;
  const S = () => STRATA;

  function PulseList({ title, items, code, metric, app }) {
    const fmt = v => {
      if (metric === "median") return S().fmtCompact(v, code);
      if (metric === "score") return v.toFixed(1);
      return v;
    };
    return (
      <div className="card pooled lift" style={{ flex: 1, minWidth: 230 }}>
        <div className="card-head" style={{ marginBottom: 12 }}>
          <span className="card-title">{title}</span>
        </div>
        <div className="col" style={{ gap: 2 }}>
          {items.map((r, i) => {
            const cd = r.countries[code];
            const val = metric === "median" ? cd.median : metric === "score" ? cd.score.total : cd.demand;
            return (
              <button key={r.id} onClick={(e) => window.openRoleMenu(r.id, e.clientX, e.clientY)}
                className="row between gap10" style={{
                  background: "transparent", border: "none", cursor: "pointer", padding: "9px 0",
                  borderBottom: i < items.length - 1 ? "1px solid var(--line)" : "none", textAlign: "left", width: "100%"
                }}
                onMouseEnter={e => e.currentTarget.style.opacity = 0.7}
                onMouseLeave={e => e.currentTarget.style.opacity = 1}>
                <span className="row gap10" style={{ minWidth: 0, alignItems: "center" }}>
                  <span className="tnum" style={{ color: "var(--t3)", fontSize: 12, fontWeight: 700, width: 14 }}>{i + 1}</span>
                  <UI.FamilyDot family={r.family} />
                  <span style={{ fontSize: 13.5, color: "var(--t1)", fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
                </span>
                <span className="tnum" style={{ fontSize: 13, fontWeight: 700, color: "#fff", whiteSpace: "nowrap" }}>{fmt(val)}</span>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  function ExploreCanvas({ app }) {
    const [mode, setMode] = useState("role"); // role | country
    const [metric, setMetric] = useState("median");
    const country = app.country, setCountry = app.setCountry;
    const [roleId, setRoleId] = useState("ml-eng");
    const [expanded, setExpanded] = useState(null);
    useEffect(() => { setExpanded(null); }, [mode, metric, country, roleId]);

    const metricMeta = {
      median: { label: "Median pay", fmt: (v, c) => S().fmtCompact(v, c) },
      demand: { label: "Market demand", fmt: v => v },
      score: { label: "Job Score", fmt: v => v.toFixed(1) },
    };

    let items, sub, fmtFn;
    if (mode === "role") {
      const list = [...S().roles].map(r => {
        const cd = r.countries[country];
        const v = metric === "median" ? cd.median : metric === "score" ? cd.score.total : cd.demand;
        return { label: r.name, value: v, geom: v, id: r.id };
      }).sort((a, b) => b.value - a.value);
      items = list;
      sub = `${metricMeta[metric].label} across all roles · ${S().C[country].name}`;
      fmtFn = v => metricMeta[metric].fmt(v, country);
    } else {
      const role = S().roleById(roleId);
      const usePPP = metric === "median" && app.ppp;
      const list = S().COUNTRIES.map(co => {
        const cd = role.countries[co.code];
        const pppMed = S().pppUSD(cd.median, co.code);
        let v = metric === "median" ? cd.median : metric === "score" ? cd.score.total : cd.demand;
        if (usePPP) v = pppMed;
        // median bars sized by PPP so cross-currency lengths are fair
        const geom = metric === "median" ? pppMed : v;
        return { label: co.name, value: v, geom, code: co.code };
      }).sort((a, b) => b.geom - a.geom);
      items = list;
      sub = `${metricMeta[metric].label} for ${role.name} · all 7 countries${usePPP ? " · PPP" : metric === "median" ? " · native currency" : ""}`;
      fmtFn = v => metric === "median" ? (usePPP ? "◊" + Math.round(v / 1000) + "k" : metricMeta[metric].fmt(v, items[0].code)) : metricMeta[metric].fmt(v);
    }

    return (
      <div className="card" style={{ padding: 28 }}>
        <div className="row between wrap-f gap16" style={{ marginBottom: 22 }}>
          <div>
            <div className="sec-eyebrow">Explore mode</div>
            <div className="h3 display-ink">Pick an axis. The market redraws.</div>
            <div className="small" style={{ marginTop: 6, color: "var(--t3)" }}>{sub}</div>
          </div>
          <div className="col gap10" style={{ alignItems: "flex-end" }}>
            <div className="seg">
              <button className={mode === "role" ? "on" : ""} onClick={() => setMode("role")}>Roles in a country</button>
              <button className={mode === "country" ? "on" : ""} onClick={() => setMode("country")}>One role, all countries</button>
            </div>
            <div className="row gap8 wrap-f" style={{ justifyContent: "flex-end" }}>
              <div className="seg">
                {Object.keys(metricMeta).map(m => (
                  <button key={m} className={metric === m ? "on" : ""} onClick={() => setMetric(m)}>{metricMeta[m].label}</button>
                ))}
              </div>
              {mode === "role"
                ? <UI.CountrySelect value={country} onChange={setCountry} />
                : <RolePicker value={roleId} onChange={setRoleId} />}
            </div>
          </div>
        </div>
        <div className="col" style={{ gap: 6 }}>
          {items.map((it) => {
            const key = it.id || it.code;
            const isOpen = expanded === key;
            const mx = Math.max(...items.map(x => x.geom));
            const accent = (it.id === "ml-eng" || it.code === app.country) ? "linear-gradient(90deg,#0033ff,#7aa0ff)" : "linear-gradient(90deg,#1d3a8f,#3a63c9)";
            const role = mode === "role" ? S().roleById(it.id) : S().roleById(roleId);
            const cd = mode === "role" ? role.countries[country] : role.countries[it.code];
            return (
              <div key={key} style={{ borderRadius: 10, background: isOpen ? "rgba(42,91,255,0.05)" : "transparent", border: "1px solid " + (isOpen ? "rgba(74,124,255,0.22)" : "transparent"), transition: "background 0.2s", overflow: "hidden" }}>
                <div className="row gap12" style={{ alignItems: "center", cursor: "pointer", padding: "7px 10px" }}
                  onClick={() => setExpanded(isOpen ? null : key)}
                  onMouseEnter={e => { if (!isOpen) e.currentTarget.parentElement.style.background = "rgba(255,255,255,0.025)"; }}
                  onMouseLeave={e => { if (!isOpen) e.currentTarget.parentElement.style.background = "transparent"; }}>
                  <div className="row gap8" style={{ width: 150, alignItems: "center", minWidth: 0 }}>
                    {mode === "role" ? <UI.FamilyDot family={role.family} /> : <UI.CountryDot code={it.code} size={12} />}
                    <span style={{ fontSize: 13, color: "var(--t1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{it.label}</span>
                  </div>
                  <div style={{ flex: 1, height: 24, background: "rgba(255,255,255,0.05)", borderRadius: 7, overflow: "hidden" }}>
                    <div className="glowbar" style={{ width: `${(it.geom / mx) * 100}%`, height: "100%", borderRadius: 7, background: accent, boxShadow: "0 0 14px rgba(74,124,255,0.4)", transition: "width 0.7s cubic-bezier(0.2,0.7,0.2,1)", minWidth: 4 }} />
                  </div>
                  <div className="tnum row gap8" style={{ width: 100, justifyContent: "flex-end", alignItems: "center", fontSize: 13, fontWeight: 700, color: "#fff" }}>
                    {fmtFn(it.value)}
                    <span style={{ color: "var(--t3)", fontSize: 11, transform: isOpen ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>▾</span>
                  </div>
                </div>
                {isOpen && (
                  <div style={{ padding: "6px 14px 16px", animation: "fadeUp 0.25s ease" }}>
                    <div className="row between" style={{ marginBottom: 8 }}>
                      <span className="eyebrow" style={{ fontSize: 9.5 }}>{metric === "median" ? "SALARY" : "DEMAND"} · {mode === "role" ? "2017–2025" : it.label + " · 2017–2025"}</span>
                      <span className="row gap12" style={{ alignItems: "center" }}>
                        <span className="small" style={{ color: "var(--t3)" }}>demand {cd.demand} · interest {cd.interest}</span>
                        <UI.ConfidenceBadge data={cd} align="right" />
                      </span>
                    </div>
                    {metric === "demand"
                      ? <Charts.DemandTrend series={cd.demandSeries} height={120} />
                      : <Charts.SalaryTrend series={cd.series} code={mode === "role" ? country : it.code} height={140} />}
                    <div className="row between mt12 wrap-f gap10">
                      <span className="small" style={{ color: "var(--t2)" }}>{mode === "role" ? role.blurb : `${role.name} in ${it.label}`}</span>
                      <button className="pill sm solid" onClick={(e) => { e.stopPropagation(); if (mode === "country") app.setCountry(it.code); app.go("roles", { roleId: role.id }); }}>Open dashboard →</button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="row gap16 mt20" style={{ color: "var(--t3)", fontSize: 12, flexWrap: "wrap" }}>
          <span>{mode === "role" ? "Click any role to expand its trend — then open its dashboard." : "Bars sized by purchasing power; figures in native currency. Click a country to expand."}</span>
          {mode === "country" && metric === "median" && (
            <button className="pill sm ghost" onClick={() => app.setPpp(!app.ppp)}>{app.ppp ? "PPP-adjusted ✓" : "Show PPP-adjusted"}</button>
          )}
        </div>
      </div>
    );
  }

  function RolePicker({ value, onChange }) {
    const [open, setOpen] = useState(false);
    const ref = React.useRef(null);
    React.useEffect(() => {
      const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
      document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
    }, []);
    const role = S().roleById(value);
    return (
      <div ref={ref} style={{ position: "relative" }}>
        <button className="pill" onClick={() => setOpen(o => !o)}>
          <UI.FamilyDot family={role.family} /><span>{role.name}</span><span style={{ color: "var(--t3)", fontSize: 10 }}>▾</span>
        </button>
        {open && (
          <div style={{ position: "absolute", top: "calc(100% + 8px)", right: 0, zIndex: 80, width: 250, maxHeight: 320, overflowY: "auto",
            background: "rgba(16,18,26,0.97)", border: "1px solid var(--glass-line)", borderRadius: 14, padding: 6,
            boxShadow: "0 24px 60px rgba(0,0,0,0.6)", backdropFilter: "blur(20px)" }}>
            {S().roles.map(r => (
              <button key={r.id} onClick={() => { onChange(r.id); setOpen(false); }} className="row gap10"
                style={{ width: "100%", padding: "9px 11px", borderRadius: 9, border: "none", cursor: "pointer",
                  background: r.id === value ? "rgba(42,91,255,0.2)" : "transparent", color: "var(--t1)",
                  fontFamily: "var(--font)", fontSize: 13, fontWeight: 600, textAlign: "left", alignItems: "center" }}
                onMouseEnter={e => { if (r.id !== value) e.currentTarget.style.background = "rgba(255,255,255,0.05)"; }}
                onMouseLeave={e => { if (r.id !== value) e.currentTarget.style.background = "transparent"; }}>
                <UI.FamilyDot family={r.family} /> {r.name}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ---- live market ticker: the whole market streaming by (presentation of
  // already-hydrated STRATA data; chips open the existing role quick-menu) ----
  function Ticker({ app }) {
    const code = app.country;
    const items = S().roles.map(r => {
      const cd = r.countries[code];
      const prev = cd.series[cd.series.length - 2].value;
      const yoy = prev ? ((cd.median - prev) / prev) * 100 : 0;
      return { id: r.id, name: r.name, median: cd.median, yoy };
    });
    const Chip = ({ it }) => (
      <button className="tick-chip" onClick={(e) => window.openRoleMenu(it.id, e.clientX, e.clientY)}>
        <span className="tick-name">{it.name}</span>
        <span className="tick-val tnum">{S().fmtCompact(it.median, code)}</span>
        <span className={"tick-delta tnum " + (it.yoy >= 0 ? "up" : "down")}>
          {it.yoy >= 0 ? "▲" : "▼"} {Math.abs(it.yoy).toFixed(1)}%
        </span>
      </button>
    );
    return (
      <div className="ticker">
        <span className="ticker-label"><span className="tick-dot"></span>LIVE · {code}</span>
        <div className="ticker-clip">
          <div className="ticker-track">
            <div className="tick-set">{items.map(it => <Chip key={it.id} it={it} />)}</div>
            <div className="tick-set" aria-hidden="true">{items.map(it => <Chip key={"d" + it.id} it={it} />)}</div>
          </div>
        </div>
      </div>
    );
  }

  function Explore({ app }) {
    const code = app.country;
    const mp = S().marketPulse[code];
    const globeSize = Math.min(480, Math.max(330, (typeof window !== "undefined" ? window.innerWidth : 1200) * 0.32));

    return (
      <div className="surface-enter">
        {/* ---- atmospheric hero ---- */}
        <div style={{ position: "relative", minHeight: "min(84vh, 780px)", display: "flex", alignItems: "center" }}>
          <div style={{ position: "absolute", right: "clamp(10px, 3vw, 56px)", top: "50%", transform: "translateY(-50%)", zIndex: 3 }}>
            <UI.InteractiveGlobe size={globeSize} active={code} onSelect={app.setCountry} />
            <div className="coords" style={{ textAlign: "center", marginTop: 8, opacity: 0.6 }}>DRAG TO SPIN · TAP A COUNTRY</div>
          </div>
          <div className="wrap" style={{ position: "relative", zIndex: 2, width: "100%", pointerEvents: "none" }}>
            <div style={{ maxWidth: 680, pointerEvents: "auto" }}>
              <div className="eyebrow enter" style={{ animationDelay: "0.05s" }}>Global tech job-market intelligence · 7 countries</div>
              <h1 className="display enter" style={{ marginTop: 22, animationDelay: "0.12s" }}>
                <span className="display-ink">The whole tech<br />job market,</span><br /><span className="display-ghost">worth exploring.</span>
              </h1>
              <p className="body enter" style={{ maxWidth: 430, marginTop: 26, fontSize: 16.5, animationDelay: "0.2s" }}>
                Salaries, demand, skills and rankings for every tech role across 7 markets. Spin the globe and tap a country — the whole page redraws around <strong style={{ color: "var(--t1)" }}>{S().C[code].name}</strong>.
              </p>
              <div className="row gap10 mt32 wrap-f enter" style={{ animationDelay: "0.28s" }}>
                <button className="pill solid" onClick={() => app.go("roles")}>Browse roles →</button>
                <button className="pill" onClick={() => document.getElementById("explore-canvas")?.scrollIntoView({ behavior: "smooth" })}>Explore the market</button>
                <button className="pill ghost" onClick={() => app.go("resume")}>Drop a résumé</button>
              </div>
              <div className="row gap24 mt40 enter wrap-f" style={{ animationDelay: "0.36s", color: "var(--t3)" }}>
                {[["16", "tech roles"], ["7", "countries"], ["9 yrs", "of trend data"], ["100%", "traceable"]].map(([n, l]) => (
                  <div key={l}><div className="tnum big-lum" style={{ fontSize: 22, fontWeight: 800 }}>{n}</div><div style={{ fontSize: 11.5, letterSpacing: "0.04em" }}>{l}</div></div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* ---- live market ticker ---- */}
        <div className="wrap">
          <Ticker app={app} />
        </div>

        {/* ---- market pulse ---- */}
        <div className="wrap" style={{ marginTop: 20 }}>
          <div className="sec-head">
            <div>
              <div className="sec-eyebrow">Market pulse</div>
              <div className="h2 display-ink">What's moving in <span className="display-ghost">{S().C[code].name}</span></div>
            </div>
            <UI.CountrySelect value={code} onChange={app.setCountry} />
          </div>
          <div className="row gap16 wrap-f" style={{ alignItems: "stretch" }}>
            <PulseList title="Hottest demand" items={mp.hottest} code={code} metric="demand" app={app} />
            <PulseList title="Highest paid" items={mp.topPay} code={code} metric="median" app={app} />
            <PulseList title="Fastest rising" items={mp.rising} code={code} metric="demand" app={app} />
            <PulseList title="Top Job Score" items={mp.topScore} code={code} metric="score" app={app} />
          </div>
        </div>

        {/* ---- explore canvas ---- */}
        <div className="wrap" id="explore-canvas" style={{ marginTop: 56, scrollMarginTop: 90 }}>
          <ExploreCanvas app={app} />
        </div>
      </div>
    );
  }

export { Explore, Ticker };
