import React from "react";
import { STRATA } from "../data/mock.js";
import { UI } from "./ui.jsx";
import { Charts } from "./charts.jsx";
/* ============================================================
   strata — Countries surface
   ============================================================ */
  const { useState } = React;
  const S = () => STRATA;

  function countryStat(code) {
    const meds = S().roles.map(r => r.countries[code]);
    const avgPPP = meds.reduce((a, r) => a + S().pppUSD(r.median, code), 0) / meds.length;
    const disclose = meds.reduce((a, r) => a + r.transparency, 0) / meds.length;
    const growth = meds.reduce((a, r) => a + (r.series[r.series.length - 1].value / r.series[0].value - 1), 0) / meds.length;
    return { avgPPP, disclose, growth };
  }

  function Countries({ app }) {
    const [code, setCode] = useState(app.country);
    const c = S().C[code];
    const st = countryStat(code);
    const ui = UI;
    const byDemand = [...S().roles].sort((a, b) => b.countries[code].demand - a.countries[code].demand);
    const byPay = [...S().roles].sort((a, b) => b.countries[code].median - a.countries[code].median);
    const byGrowth = [...S().roles].map(r => ({ r, g: r.countries[code].series[8].value / r.countries[code].series[3].value - 1 })).sort((a, b) => b.g - a.g);
    const byDisclosure = [...S().roles].sort((a, b) => b.countries[code].transparency - a.countries[code].transparency);

    return (
      <div className="wrap-wide surface-enter">
        <div className="sec-head" style={{ marginBottom: 24 }}>
          <div><div className="sec-eyebrow">Countries</div><div className="h1" style={{ color: "#fff" }}>Markets, end to end</div>
            <div className="body" style={{ marginTop: 8, maxWidth: 520 }}>A standing dashboard per market — pay landscape, what's moving, and how openly salaries are disclosed.</div></div>
        </div>

        {/* 7-country strip */}
        <div className="card country-strip-card" style={{ marginBottom: 16, overflowX: "auto" }}>
          <div className="card-sub" style={{ marginBottom: 16 }}>AVERAGE TECH PAY · PPP-ADJUSTED · TAP A MARKET</div>
          <div className="row gap10 country-strip" style={{ minWidth: 700 }}>
            {S().COUNTRIES.map(co => {
              const s = countryStat(co.code);
              const mx = Math.max(...S().COUNTRIES.map(c2 => countryStat(c2.code).avgPPP));
              return (
                <button key={co.code} onClick={() => setCode(co.code)} className="col gap10" style={{ flex: 1, background: code === co.code ? "rgba(42,91,255,0.1)" : "transparent", border: "1px solid " + (code === co.code ? "rgba(74,124,255,0.35)" : "var(--line)"), borderRadius: 12, padding: "14px 12px", cursor: "pointer", alignItems: "stretch" }}>
                  <div className="row gap8" style={{ alignItems: "center" }}><ui.CountryDot code={co.code} size={12} /><span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--t1)", whiteSpace: "nowrap" }}>{co.code}</span></div>
                  <div className="tnum" style={{ fontSize: 18, fontWeight: 700, color: "#fff" }}>◊{Math.round(s.avgPPP / 1000)}k</div>
                  <div style={{ height: 4, borderRadius: 9, background: "rgba(255,255,255,0.07)", overflow: "hidden" }}><div style={{ width: `${(s.avgPPP / mx) * 100}%`, height: "100%", background: "linear-gradient(90deg,#0033ff,#4a7cff)" }} /></div>
                </button>
              );
            })}
          </div>
        </div>

        {/* country header stats */}
        <div className="grid" style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.2fr", gap: 16, marginBottom: 16 }}>
          <div className="card pooled" style={{ justifyContent: "space-between" }}>
            <div className="row gap12" style={{ alignItems: "center" }}><ui.CountryDot code={code} size={20} /><div className="h2" style={{ color: "#fff" }}>{c.name}</div></div>
            <div className="row between mt20"><div><div className="stat-label">Currency</div><div style={{ fontSize: 18, fontWeight: 700, color: "#fff", marginTop: 4 }}>{c.cur} {c.curCode}</div></div>
              <button className="pill sm" onClick={() => app.go("compare", { mode: "mirror" })}>Mirror →</button></div>
          </div>
          <div className="card"><span className="stat-label">Avg pay (PPP)</span><div className="tnum" style={{ fontSize: 30, fontWeight: 700, color: "#fff", marginTop: 12 }}>◊{Math.round(st.avgPPP / 1000)}k</div><div className="small mt8" style={{ color: "var(--t3)" }}>across 16 roles</div></div>
          <div className="card"><span className="stat-label">5-yr growth</span><div className="tnum" style={{ fontSize: 30, fontWeight: 700, color: "var(--good)", marginTop: 12 }}>+{Math.round(st.growth * 100)}%</div><div className="small mt8" style={{ color: "var(--t3)" }}>median, blended</div></div>
          <div className="card pooled">
            <div className="row gap16" style={{ alignItems: "center" }}>
              <Charts.Donut value={st.disclose} size={84} />
              <div><div className="card-title">Pay Transparency Index</div><div className="small mt8" style={{ color: "var(--t2)", maxWidth: 150 }}>of real postings disclose salary. Where it's low, figures carry a low-confidence badge — never hidden.</div></div>
            </div>
          </div>
        </div>

        {/* pay landscape + top demand */}
        <div className="grid" style={{ gridTemplateColumns: "1.5fr 1fr", gap: 16, marginBottom: 16 }}>
          <div className="card pooled">
            <div className="card-head"><div><div className="card-title">Pay landscape</div><div className="card-sub">Median by role · {c.name} · native currency</div></div></div>
            <Charts.RankBars items={byPay.slice(0, 9).map(r => ({ label: r.name, value: r.countries[code].median, id: r.id }))} fmt={v => S().fmtCompact(v, code)} onItemClick={(it, e) => window.openRoleMenu(it.id, e.clientX, e.clientY)} />
          </div>
          <div className="card">
            <div className="card-head"><div><div className="card-title">Hottest demand</div><div className="card-sub">Most-wanted roles</div></div></div>
            <div className="col" style={{ gap: 2 }}>
              {byDemand.slice(0, 7).map((r, i) => (
                <button key={r.id} onClick={(e) => window.openRoleMenu(r.id, e.clientX, e.clientY)} className="row between" style={{ background: "transparent", border: "none", cursor: "pointer", padding: "9px 0", borderBottom: i < 6 ? "1px solid var(--line)" : "none", width: "100%" }}>
                  <span className="row gap10"><span className="tnum" style={{ color: "var(--t3)", width: 12, fontWeight: 700 }}>{i + 1}</span><ui.FamilyDot family={r.family} /><span style={{ fontSize: 13.5, color: "var(--t1)" }}>{r.name}</span></span>
                  <span className="tag">demand {r.countries[code].demand}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* fastest moving + transparency by role */}
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <div className="card">
            <div className="card-head"><div><div className="card-title">Fastest-moving areas</div><div className="card-sub">Demand growth, 2020 → 2025</div></div></div>
            <div className="col gap10">
              {byGrowth.slice(0, 5).map(({ r, g }) => (
                <button key={r.id} onClick={(e) => window.openRoleMenu(r.id, e.clientX, e.clientY)} className="row between" style={{ alignItems: "center", background: "transparent", border: "none", cursor: "pointer", padding: "4px 0", width: "100%" }}>
                  <span className="row gap10" style={{ fontSize: 13.5, color: "var(--t1)" }}><ui.FamilyDot family={r.family} />{r.name}</span>
                  <span className="delta up">↑ {Math.round(g * 100)}%</span>
                </button>
              ))}
            </div>
          </div>
          <div className="card pooled">
            <div className="card-head"><div><div className="card-title">Disclosure by role</div><div className="card-sub">Where pay is most openly posted</div></div></div>
            <div className="col gap10">
              {byDisclosure.slice(0, 5).map(r => {
                const t = r.countries[code].transparency;
                return (
                  <div key={r.id} className="row gap12" style={{ alignItems: "center" }}>
                    <span style={{ width: 150, fontSize: 13, color: "var(--t1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
                    <div style={{ flex: 1, height: 8, background: "rgba(255,255,255,0.06)", borderRadius: 9, overflow: "hidden" }}>
                      <div style={{ width: `${t * 100}%`, height: "100%", borderRadius: 9, background: t >= 0.6 ? "var(--good)" : t >= 0.4 ? "var(--warn)" : "var(--bad)" }} />
                    </div>
                    <span className="tnum" style={{ width: 40, textAlign: "right", fontSize: 13, fontWeight: 700, color: "#fff" }}>{Math.round(t * 100)}%</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
        <div style={{ height: 40 }}></div>
      </div>
    );
  }

export { Countries };
