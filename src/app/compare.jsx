import React from "react";
import { STRATA } from "../data/mock.js";
import { UI } from "./ui.jsx";
import { Charts } from "./charts.jsx";
/* ============================================================
   strata — Compare surface
   ============================================================ */
  const { useState } = React;
  const S = () => STRATA;
  const Uc = UI, Cc = Charts;

  function RolePickerInline({ onPick, exclude = [], label = "+ Add role" }) {
    const [open, setOpen] = useState(false);
    const ref = React.useRef(null);
    React.useEffect(() => { const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }; document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h); }, []);
    const avail = S().roles.filter(r => !exclude.includes(r.id));
    return (
      <div ref={ref} style={{ position: "relative" }}>
        <button className="pill" onClick={() => setOpen(o => !o)}>{label}</button>
        {open && (
          <div style={{ position: "absolute", top: "calc(100% + 8px)", left: 0, zIndex: 80, width: 250, maxHeight: 300, overflowY: "auto", background: "rgba(16,18,26,0.97)", border: "1px solid var(--glass-line)", borderRadius: 14, padding: 6, boxShadow: "0 24px 60px rgba(0,0,0,0.6)", backdropFilter: "blur(20px)" }}>
            {avail.map(r => (
              <button key={r.id} onClick={() => { onPick(r.id); setOpen(false); }} className="row gap10" style={{ width: "100%", padding: "9px 11px", borderRadius: 9, border: "none", cursor: "pointer", background: "transparent", color: "var(--t1)", fontFamily: "var(--font)", fontSize: 13, fontWeight: 600, textAlign: "left", alignItems: "center" }}
                onMouseEnter={e => e.currentTarget.style.background = "rgba(255,255,255,0.05)"} onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                <Uc.FamilyDot family={r.family} /> {r.name}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  /* -------- Role vs Role -------- */
  function RoleVsRole({ app }) {
    const [country, setCountry] = useState(app.country);
    const ids = app.tray.length ? app.tray : ["ml-eng", "swe"];
    const roles = ids.map(id => S().roleById(id)).filter(Boolean);
    const metrics = [
      { k: "Median pay", get: r => S().fmtCur(r.countries[country].median, country), raw: r => r.countries[country].median },
      { k: "Job Score", get: r => r.countries[country].score.total.toFixed(1) + "  ·  top " + r.countries[country].score.pctile + "%", raw: r => r.countries[country].score.total },
      { k: "Market demand", get: r => r.countries[country].demand, raw: r => r.countries[country].demand },
      { k: "Learner interest", get: r => r.countries[country].interest, raw: r => r.countries[country].interest },
      { k: "Top skill", get: r => r.skills[0].name },
      { k: "Entry barrier", get: r => { const adv = r.skills.filter(s => s.level === "A").length; return adv >= 3 ? "High" : adv === 2 ? "Medium" : "Approachable"; } },
    ];

    return (
      <div>
        <div className="row between wrap-f gap12" style={{ marginBottom: 20 }}>
          <div className="row gap10 wrap-f" style={{ alignItems: "center" }}>
            {roles.map(r => (
              <span key={r.id} className="pill active" style={{ paddingRight: 8 }}>
                <Uc.FamilyDot family={r.family} />{r.name}
                <span onClick={() => app.setTray(app.tray.filter(x => x !== r.id))} style={{ cursor: "pointer", color: "var(--t3)", marginLeft: 4, fontSize: 14 }}>×</span>
              </span>
            ))}
            {roles.length < 4 && <RolePickerInline onPick={id => app.setTray([...new Set([...ids, id])].slice(0, 4))} exclude={ids} />}
          </div>
          <Uc.CountrySelect value={country} onChange={setCountry} />
        </div>

        {roles.length >= 2 && (
          <div className="card pooled" style={{ marginBottom: 16, overflowX: "auto" }}>
            <table className="tbl" style={{ minWidth: 520 }}>
              <thead><tr><th>Metric</th>{roles.map(r => <th key={r.id} className="num" style={{ color: "var(--t1)", fontSize: 13, textTransform: "none", letterSpacing: 0 }}>{r.name}</th>)}</tr></thead>
              <tbody>
                {metrics.map(m => {
                  const raws = m.raw ? roles.map(m.raw) : null;
                  const best = raws ? Math.max(...raws) : null;
                  return (
                    <tr key={m.k} style={{ cursor: "default" }}>
                      <td style={{ color: "var(--t3)" }}>{m.k}</td>
                      {roles.map((r, i) => (
                        <td key={r.id} className="num" style={{ fontWeight: raws && raws[i] === best ? 700 : 500, color: raws && raws[i] === best ? "#fff" : "var(--t2)" }}>
                          {m.get(r)} {raws && raws[i] === best && <span style={{ color: "var(--sky)", fontSize: 10, marginLeft: 4 }}>▲</span>}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {roles.length >= 2 && <RoleOverlay roles={roles} country={country} />}
        {roles.length < 2 && <Uc.Empty icon="⊕" title="Pin at least two roles" sub="Add roles to hold them side by side. You can pin up to four of anything." />}
      </div>
    );
  }

  // per-chart role filter chips
  function OverlayChart({ roles, cols, title, sub, badge, children }) {
    const [vis, setVis] = useState(() => new Set(roles.map(r => r.id)));
    React.useEffect(() => { setVis(new Set(roles.map(r => r.id))); }, [roles.map(r => r.id).join(",")]);
    const toggle = id => setVis(prev => { const n = new Set(prev); if (n.has(id)) { if (n.size > 1) n.delete(id); } else n.add(id); return new Set(n); });
    const shown = roles.filter(r => vis.has(r.id));
    return (
      <div className="card pooled">
        <div className="card-head" style={{ alignItems: "flex-start" }}>
          <div><div className="card-title">{title}</div><div className="card-sub">{sub}</div></div>
          {badge}
        </div>
        <div className="chips" style={{ marginBottom: 16 }}>
          {roles.map((r, i) => {
            const on = vis.has(r.id);
            return (
              <button key={r.id} onClick={() => toggle(r.id)} className="pill sm" style={{
                background: on ? cols[i] + "26" : "transparent", borderColor: on ? cols[i] + "77" : "var(--line-2)",
                color: on ? "#fff" : "var(--t3)", opacity: on ? 1 : 0.6
              }}>
                <span style={{ width: 8, height: 8, borderRadius: 9, background: cols[i], display: "inline-block", opacity: on ? 1 : 0.4 }}></span>
                {r.name}
              </button>
            );
          })}
        </div>
        {children(shown, shown.map(r => cols[roles.indexOf(r)]))}
      </div>
    );
  }

  // multi-role overlay charts — 2×2, each chart independently selectable (1–4 roles)
  function RoleOverlay({ roles, country }) {
    const cols = Charts.SERIES_COLORS;
    const slopes = roles.map(r => { const s = r.countries[country].series; return s[s.length - 1].value / s[0].value; });
    const spread = Math.max(...slopes) - Math.min(...slopes);
    const axes = ["Pay", "Demand", "Opportunity", "Durability", "Growth", "Ceiling"];
    const shape = r => {
      const cd = r.countries[country];
      const dura = Math.round(r.skills.reduce((a, s) => a + s.dura, 0) / r.skills.length);
      const growth = Math.min(100, (cd.series[8].value / cd.series[0].value - 1) * 130);
      const ceiling = Math.min(100, r.ladder[r.ladder.length - 1][1] * 52);
      const pay = Math.min(100, S().pppUSD(cd.median, country) / 2000);
      return [pay, cd.demand, cd.score.opp * 10, dura, growth, ceiling];
    };
    return (
      <div className="grid" style={{ gap: 16 }}>
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <OverlayChart roles={roles} cols={cols} title="Role shape" sub={`Six dimensions overlaid · ${S().C[country].name}`}>
            {(shown, sc) => (
              <div style={{ display: "grid", placeItems: "center" }}>
                <Charts.RadarMulti axes={axes} series={shown.map((r, i) => ({ label: r.name, color: sc[i], values: shape(r) }))} size={290} />
              </div>
            )}
          </OverlayChart>
          <OverlayChart roles={roles} cols={cols} title="Salary trajectories" sub="Indexed to each role's peak"
            badge={<span className="tag" style={{ color: spread > 0.18 ? "var(--warn)" : "var(--good)" }}>{spread > 0.18 ? "Diverging" : "Converging"}</span>}>
            {(shown, sc) => <Charts.MultiLine series={shown.map((r, i) => ({ label: r.name, color: sc[i], points: r.countries[country].series }))} height={228} normalize fmtFn={v => Math.round(v) + "%"} />}
          </OverlayChart>
        </div>
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <OverlayChart roles={roles} cols={cols} title="Demand trajectories" sub="Indexed demand 0–100 · 2017–2025">
            {(shown, sc) => <Charts.MultiLine series={shown.map((r, i) => ({ label: r.name, color: sc[i], points: r.countries[country].demandSeries }))} height={228} fmtFn={v => Math.round(v)} />}
          </OverlayChart>
          <OverlayChart roles={roles} cols={cols} title="Opportunity map" sub="Demand vs learner interest — top-left is underserved">
            {(shown, sc) => <Charts.Scatter quadrant xLabel="Learner interest" yLabel="Market demand" height={228}
              points={shown.map((r, i) => ({ label: r.name.split(" ").slice(-1)[0] === "Engineer" ? r.name.replace(" Engineer", "") : r.name, x: r.countries[country].interest, y: r.countries[country].demand, color: sc[i] }))} />}
          </OverlayChart>
        </div>
      </div>
    );
  }

  // per-chart country filter chips (parallel to OverlayChart, for Country vs Country)
  function CountryOverlayChart({ codes, cols, title, sub, children }) {
    const [vis, setVis] = useState(() => new Set(codes));
    React.useEffect(() => { setVis(new Set(codes)); }, [codes.join(",")]);
    const toggle = c => setVis(prev => { const n = new Set(prev); if (n.has(c)) { if (n.size > 1) n.delete(c); } else n.add(c); return new Set(n); });
    const shown = codes.filter(c => vis.has(c));
    return (
      <div className="card pooled">
        <div className="card-head"><div><div className="card-title">{title}</div><div className="card-sub">{sub}</div></div></div>
        <div className="chips" style={{ marginBottom: 16 }}>
          {codes.map((code, i) => {
            const on = vis.has(code);
            return (
              <button key={code} onClick={() => toggle(code)} className="pill sm" style={{
                background: on ? cols[i % 4] + "26" : "transparent", borderColor: on ? cols[i % 4] + "77" : "var(--line-2)",
                color: on ? "#fff" : "var(--t3)", opacity: on ? 1 : 0.6
              }}>
                <Uc.CountryDot code={code} size={10} />{S().C[code].name}
              </button>
            );
          })}
        </div>
        {children(shown, shown.map(c => cols[codes.indexOf(c) % 4]))}
      </div>
    );
  }

  /* -------- Country vs Country (one role) -------- */
  function CountryCompare({ app }) {
    const [roleId, setRoleId] = useState(app.route.roleId || "ml-eng");
    const [picked, setPicked] = useState(["IN", "US", "GB", "DE"]);
    const [metric, setMetric] = useState("median");
    const role = S().roleById(roleId);
    const toggle = c => setPicked(p => p.includes(c) ? p.filter(x => x !== c) : [...p, c]);

    const items = picked.map(code => {
      const cd = role.countries[code];
      const pppMed = S().pppUSD(cd.median, code);
      let v = metric === "median" ? cd.median : metric === "score" ? cd.score.total : cd.demand;
      if (metric === "median" && app.ppp) v = pppMed;
      // geometry: median bars always sized by PPP for fairness
      const geom = metric === "median" ? pppMed : v;
      return { label: S().C[code].name, value: v, geom, code, native: cd.median };
    }).sort((a, b) => b.geom - a.geom);
    const mx = Math.max(...items.map(i => i.geom));

    return (
      <div>
        <div className="row between wrap-f gap12" style={{ marginBottom: 18 }}>
          <RolePickerInline onPick={setRoleId} exclude={[]} label={<span className="row gap8"><Uc.FamilyDot family={role.family} />{role.name} ▾</span>} />
          <div className="row gap10 wrap-f">
            <div className="seg">
              <button className={metric === "median" ? "on" : ""} onClick={() => setMetric("median")}>Pay</button>
              <button className={metric === "demand" ? "on" : ""} onClick={() => setMetric("demand")}>Demand</button>
              <button className={metric === "score" ? "on" : ""} onClick={() => setMetric("score")}>Job Score</button>
            </div>
            {metric === "median" && <button className="pill sm ghost" onClick={() => app.setPpp(!app.ppp)}>{app.ppp ? "PPP ✓" : "Nominal"}</button>}
          </div>
        </div>

        <div className="chips" style={{ marginBottom: 20 }}>
          {S().COUNTRIES.map(co => (
            <button key={co.code} className={"pill sm" + (picked.includes(co.code) ? " active" : " ghost")} onClick={() => toggle(co.code)}>
              <Uc.CountryDot code={co.code} size={10} />{co.name}
            </button>
          ))}
        </div>

        <div className="card pooled">
          <div className="card-head"><div><div className="card-title">{role.name} · {metric === "median" ? "median pay" : metric === "demand" ? "market demand" : "Job Score"}</div>
            <div className="card-sub">{metric === "median" ? (app.ppp ? "PPP-adjusted to international $ — comparable purchasing power" : "Each country's own currency — no FX conversion") : "Indexed comparison across selected markets"}</div></div></div>
          <div className="col" style={{ gap: 11 }}>
            {items.map(it => (
              <div key={it.code} className="row gap12" style={{ alignItems: "center" }}>
                <span style={{ width: 150 }}><Uc.CountryTag code={it.code} /></span>
                <div style={{ flex: 1, height: 22, background: "rgba(255,255,255,0.05)", borderRadius: 7, overflow: "hidden" }}>
                  <div className="glowbar" style={{ width: `${(it.geom / mx) * 100}%`, height: "100%", borderRadius: 7, background: "linear-gradient(90deg,#0033ff,#7aa0ff)", boxShadow: "0 0 14px rgba(74,124,255,0.4)", transition: "width 0.7s" }} />
                </div>
                <span className="tnum" style={{ width: 130, textAlign: "right", fontSize: 14, fontWeight: 700, color: "#fff" }}>
                  {metric === "median" ? (app.ppp ? "◊" + Math.round(it.value / 1000) + "k" : S().fmtCompact(it.native, it.code)) : metric === "score" ? it.value.toFixed(1) : it.value}
                </span>
              </div>
            ))}
          </div>
          {items.length === 0 && <Uc.Empty icon="◎" title="Pick some countries" sub="Select markets above to compare them." />}
        </div>

        {picked.length >= 2 && (
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
            <CountryOverlayChart codes={picked} cols={Charts.SERIES_COLORS} title="Salary trajectory by market" sub={`${role.name} · PPP-indexed so currencies compare`}>
              {(shown, sc) => (
                <Charts.MultiLine height={220}
                  series={shown.map((code, i) => ({ label: S().C[code].name, color: sc[i], points: role.countries[code].series.map(p => ({ year: p.year, value: Math.round(S().pppUSD(p.value, code)) })) }))}
                  fmtFn={v => "◊" + Math.round(v / 1000) + "k"} />
              )}
            </CountryOverlayChart>
            <CountryOverlayChart codes={picked} cols={Charts.SERIES_COLORS} title="Demand by market" sub={`${role.name} · indexed 0–100`}>
              {(shown, sc) => (
                <Charts.MultiLine height={220}
                  series={shown.map((code, i) => ({ label: S().C[code].name, color: sc[i], points: role.countries[code].demandSeries }))}
                  fmtFn={v => Math.round(v)} />
              )}
            </CountryOverlayChart>
          </div>
        )}
      </div>
    );
  }

  /* -------- Then vs Now -------- */
  function ThenVsNow({ app }) {
    const [roleId, setRoleId] = useState("ml-eng");
    const [country, setCountry] = useState(app.country);
    const [yA, setYA] = useState(2019), [yB, setYB] = useState(2025);
    const role = S().roleById(roleId);
    const cd = role.countries[country];
    const valAt = y => cd.series.find(s => s.year === y)?.value;
    const demAt = y => cd.demandSeries.find(s => s.year === y)?.value;
    const payA = valAt(yA), payB = valAt(yB), demA = demAt(yA), demB = demAt(yB);
    const entered = role.skills.filter(s => s.trend === "rising").slice(0, 3);
    const left = role.skills.filter(s => s.trend === "fading").slice(0, 2);

    return (
      <div>
        <div className="row between wrap-f gap12" style={{ marginBottom: 20 }}>
          <RolePickerInline onPick={setRoleId} label={<span className="row gap8"><Uc.FamilyDot family={role.family} />{role.name} ▾</span>} />
          <div className="row gap10 wrap-f">
            <YearPicker value={yA} onChange={setYA} /><span style={{ color: "var(--t3)" }}>vs</span><YearPicker value={yB} onChange={setYB} />
            <Uc.CountrySelect value={country} onChange={setCountry} />
          </div>
        </div>
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
          <ThenNowStat label="Median pay" then={S().fmtCur(payA, country)} now={S().fmtCur(payB, country)} delta={((payB - payA) / payA * 100)} />
          <ThenNowStat label="Market demand" then={demA} now={demB} delta={((demB - demA) / demA * 100)} />
        </div>
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
          <div className="card pooled">
            <div className="card-head"><div><div className="card-title">Salary, {yA} → {yB}</div><div className="card-sub">{role.name} · {S().C[country].name}</div></div>
              <span className={"delta " + (payB >= payA ? "up" : "down")}>{payB >= payA ? "↑" : "↓"} {Math.abs((payB - payA) / payA * 100).toFixed(0)}%</span></div>
            <Cc.SalaryTrend series={cd.series.filter(s => s.year >= yA && s.year <= yB)} code={country} height={190} />
          </div>
          <div className="card pooled">
            <div className="card-head"><div><div className="card-title">Demand, {yA} → {yB}</div><div className="card-sub">Indexed demand 0–100</div></div>
              <span className={"delta " + (demB >= demA ? "up" : "down")}>{demB >= demA ? "↑" : "↓"} {Math.abs((demB - demA) / demA * 100).toFixed(0)}%</span></div>
            <Cc.DemandTrend series={cd.demandSeries.filter(s => s.year >= yA && s.year <= yB)} height={190} />
          </div>
        </div>
        <div className="card">
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 24 }}>
            <div>
              <div className="card-title" style={{ marginBottom: 14 }}>Skills that entered <span style={{ color: "var(--good)" }}>↗</span></div>
              <div className="chips">{entered.map(s => <span key={s.name} className="tag" style={{ color: "var(--good)", borderColor: "rgba(79,217,155,0.3)" }}>{s.name}</span>)}</div>
            </div>
            <div>
              <div className="card-title" style={{ marginBottom: 14 }}>Skills fading <span style={{ color: "var(--bad)" }}>↘</span></div>
              <div className="chips">{left.length ? left.map(s => <span key={s.name} className="tag" style={{ color: "var(--bad)", borderColor: "rgba(255,107,107,0.3)" }}>{s.name}</span>) : <span className="small" style={{ color: "var(--t3)" }}>No major skill churn in this window.</span>}</div>
            </div>
          </div>
        </div>
      </div>
    );
  }
  function YearPicker({ value, onChange }) {
    return <select className="pill" style={{ appearance: "none", paddingRight: 28 }} value={value} onChange={e => onChange(+e.target.value)}>
      {S().YEARS.map(y => <option key={y} value={y} style={{ background: "#16121a", color: "#fff" }}>{y}</option>)}
    </select>;
  }
  function ThenNowStat({ label, then, now, delta }) {
    return (
      <div className="card pooled">
        <span className="stat-label">{label}</span>
        <div className="row gap16 mt16" style={{ alignItems: "baseline", flexWrap: "wrap" }}>
          <div><div className="small" style={{ color: "var(--t3)" }}>then</div><div className="tnum" style={{ fontSize: 24, fontWeight: 700, color: "var(--t2)" }}>{then}</div></div>
          <span style={{ color: "var(--t3)", fontSize: 20 }}>→</span>
          <div><div className="small" style={{ color: "var(--t3)" }}>now</div><div className="tnum" style={{ fontSize: 30, fontWeight: 700, color: "#fff" }}>{now}</div></div>
          <span className={"delta " + (delta >= 0 ? "up" : "down")} style={{ marginLeft: "auto", fontSize: 15 }}>{delta >= 0 ? "↑" : "↓"} {Math.abs(delta).toFixed(0)}%</span>
        </div>
      </div>
    );
  }

  /* -------- Market Mirror (two whole markets) -------- */
  function MarketMirror({ app }) {
    const [a, setA] = useState("US"), [b, setB] = useState("IN");
    const stat = code => {
      const meds = S().roles.map(r => S().pppUSD(r.countries[code].median, code));
      const avgPPP = meds.reduce((x, y) => x + y, 0) / meds.length;
      const disclose = S().roles.reduce((x, r) => x + r.countries[code].transparency, 0) / S().roles.length;
      const growth = S().roles.reduce((x, r) => { const s = r.countries[code].series; return x + (s[s.length - 1].value / s[0].value - 1); }, 0) / S().roles.length;
      const top = [...S().roles].sort((x, y) => y.countries[code].median - x.countries[code].median).slice(0, 5);
      return { avgPPP, disclose, growth, top };
    };
    const sa = stat(a), sb = stat(b);
    return (
      <div>
        <div className="row gap12 wrap-f" style={{ marginBottom: 22, alignItems: "center" }}>
          <Uc.CountrySelect value={a} onChange={setA} /><span style={{ color: "var(--t3)" }}>mirrored against</span><Uc.CountrySelect value={b} onChange={setB} />
        </div>
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          {[[a, sa], [b, sb]].map(([code, s]) => (
            <div key={code} className="card pooled">
              <div className="row gap10" style={{ marginBottom: 18, alignItems: "center" }}><Uc.CountryDot code={code} size={16} /><span className="h3" style={{ color: "#fff" }}>{S().C[code].name}</span></div>
              <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 18 }}>
                <div><div className="stat-label">Avg pay (PPP)</div><div className="tnum" style={{ fontSize: 24, fontWeight: 700, color: "#fff", marginTop: 4 }}>◊{Math.round(s.avgPPP / 1000)}k</div></div>
                <div><div className="stat-label">5-yr growth</div><div className="tnum" style={{ fontSize: 24, fontWeight: 700, color: "var(--good)", marginTop: 4 }}>+{Math.round(s.growth * 100)}%</div></div>
                <div className="row gap12" style={{ alignItems: "center", gridColumn: "span 2", borderTop: "1px solid var(--line)", paddingTop: 14 }}>
                  <Charts.Donut value={s.disclose} size={64} />
                  <div><div className="stat-label">Salary disclosure</div><div className="small" style={{ color: "var(--t2)", marginTop: 4, maxWidth: 180 }}>of real postings reveal pay in {S().C[code].name}.</div></div>
                </div>
              </div>
              <div className="card-sub" style={{ marginBottom: 10 }}>TOP-PAID ROLES</div>
              <div className="col" style={{ gap: 8 }}>
                {s.top.map((r, i) => (
                  <div key={r.id} className="row between" style={{ fontSize: 13 }}>
                    <span className="row gap8"><span className="tnum" style={{ color: "var(--t3)", width: 12 }}>{i + 1}</span><span style={{ color: "var(--t1)" }}>{r.name}</span></span>
                    <span className="tnum" style={{ fontWeight: 700, color: "#fff" }}>{S().fmtCompact(r.countries[code].median, code)}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className="card pooled" style={{ marginTop: 16 }}>
          <div className="card-head"><div><div className="card-title">Average tech pay over time</div><div className="card-sub">Whole-market mean · PPP-indexed · {S().C[a].name} vs {S().C[b].name}</div></div></div>
          <Charts.MultiLine height={220}
            series={[[a, "#4a7cff"], [b, "#ffcc4d"]].map(([code, color]) => ({
              label: S().C[code].name, color,
              points: S().YEARS.map((yr, yi) => ({ year: yr, value: Math.round(S().roles.reduce((acc, r) => acc + S().pppUSD(r.countries[code].series[yi].value, code), 0) / S().roles.length) }))
            }))}
            fmtFn={v => "◊" + Math.round(v / 1000) + "k"} />
          <div className="row gap16 wrap-f mt12">
            <span className="row gap6" style={{ alignItems: "center", fontSize: 12, color: "var(--t2)" }}><span style={{ width: 14, height: 3, background: "#4a7cff", borderRadius: 2, display: "inline-block" }}></span>{S().C[a].name}</span>
            <span className="row gap6" style={{ alignItems: "center", fontSize: 12, color: "var(--t2)" }}><span style={{ width: 14, height: 3, background: "#ffcc4d", borderRadius: 2, display: "inline-block" }}></span>{S().C[b].name}</span>
          </div>
        </div>
      </div>
    );
  }

  function Compare({ app }) {
    const [mode, setMode] = useState(app.route.mode === "country" ? "country" : "role");
    const modes = [["role", "Role vs Role"], ["country", "Country vs Country"], ["then", "Then vs Now"], ["mirror", "Market Mirror"]];
    return (
      <div className="wrap-wide surface-enter">
        <div className="sec-head" style={{ marginBottom: 22 }}>
          <div><div className="sec-eyebrow">Compare</div><div className="h1" style={{ color: "#fff" }}>Hold anything side by side</div>
            <div className="body" style={{ marginTop: 8, maxWidth: 520 }}>Any role × country × year. Pin up to four, switch axes freely, normalize for purchasing power. Fully unrestricted.</div></div>
        </div>
        <div className="seg" style={{ marginBottom: 26 }}>
          {modes.map(([k, l]) => <button key={k} className={mode === k ? "on" : ""} onClick={() => setMode(k)}>{l}</button>)}
        </div>
        {mode === "role" && <RoleVsRole app={app} />}
        {mode === "country" && <CountryCompare app={app} />}
        {mode === "then" && <ThenVsNow app={app} />}
        {mode === "mirror" && <MarketMirror app={app} />}
        <div style={{ height: 40 }}></div>
      </div>
    );
  }

export { Compare };
