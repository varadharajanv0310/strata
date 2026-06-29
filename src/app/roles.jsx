import React from "react";
import { STRATA } from "../data/mock.js";
import { resolveRoles } from "../data/api.js";
import { UI } from "./ui.jsx";
import { Charts } from "./charts.jsx";
/* ============================================================
   strata — Roles surface: index + Job Score board + Role Dashboard
   ============================================================ */
  const { useState, useEffect } = React;
  const S = () => STRATA;
  const Uc = UI, Cc = Charts;

  /* ---------------- Three salary lenses: advertised / realized / official ----------------
     Shown side-by-side, never blended; each carries its own source. A lens with no data
     for this role×country honestly reads "not enough data" rather than borrowing another. */
  function SalaryLenses({ lenses, code }) {
    if (!lenses) return null;
    const defs = [["Advertised", "advertised"], ["Realized", "realized"], ["Official", "official"]];
    return (
      <div className="row gap8 mt12" style={{ flexWrap: "wrap" }}>
        {defs.map(([label, key]) => {
          const L = lenses[key];
          // each lens shows its OWN currency code (advertised native / realized+official
          // source ccy) so the three are never read as comparable bare integers.
          return (
            <div key={key} className="col" style={{ flex: 1, minWidth: 92, padding: "7px 10px", borderRadius: 9,
              background: "rgba(255,255,255,0.03)", border: "1px solid var(--line)" }}>
              <span className="small" style={{ color: "var(--t3)", fontSize: 9.5, letterSpacing: 0.3, textTransform: "uppercase" }}>{label}</span>
              {L ? (
                <>
                  <span className="tnum" style={{ fontSize: 14, fontWeight: 700, color: "#fff" }}>{L.currency ? L.currency + " " : ""}{Math.round(L.median).toLocaleString()}</span>
                  <span className="small" title={`${L.source} · ${L.basis || "annual"}${L.sample ? " · n=" + L.sample.toLocaleString() : ""}`} style={{ color: "var(--t3)", fontSize: 9, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 130 }}>{L.source}</span>
                </>
              ) : (
                <span className="small" style={{ color: "var(--t3)", fontSize: 10.5, marginTop: 4 }}>not enough data</span>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  /* ---------------- Trajectory: where this role leads (roles-only adjacency) ---------------- */
  function Trajectory({ role, app }) {
    const edges = (role.trajectory || []).slice(0, 8);
    if (!edges.length) return null;
    return (
      <div className="card">
        <div className="card-head"><div><div className="card-title">Where this role leads</div><div className="card-sub">Closest roles by O*NET relatedness · roles-only, never a company path</div></div></div>
        <div className="row gap8" style={{ flexWrap: "wrap", marginTop: 6 }}>
          {edges.map(e => (
            <button key={e.to + e.type} className="pill sm" onClick={() => app.go("roles", { roleId: e.to })}
              title={`${(e.similarity * 100).toFixed(0)}% related · ${e.source}${e.type === "career_change" ? " · common move" : ""}`}
              style={{ cursor: "pointer" }}>
              {e.name} <span style={{ color: "var(--t3)", fontSize: 10, marginLeft: 4 }}>{(e.similarity * 100).toFixed(0)}%</span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  /* ---------------- Importance-weighted skills (O*NET/ESCO core vs peripheral) ---------------- */
  const _slug = (n) => (n || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  function ImportanceSkills({ role }) {
    const rows = (role.importance || []).slice(0, 8);
    if (!rows.length) return null;
    const adopt = S().skillAdoption || {};
    const prem = S().skillPremiums || {};
    return (
      <div className="card">
        <div className="card-head"><div><div className="card-title">What matters most</div><div className="card-sub">Importance · ▲ adoption momentum · 💲 pay premium (hedonic)</div></div></div>
        <div className="col" style={{ gap: 7, marginTop: 4 }}>
          {rows.map(r => {
            const a = adopt[_slug(r.skill)];
            const mom = a && a.momentum != null ? a.momentum : null;
            const pp = prem[_slug(r.skill)];
            return (
              <div key={r.skill} className="row gap10" style={{ alignItems: "center" }}>
                <span style={{ width: 100, fontSize: 12.5, color: "var(--t1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.skill}</span>
                <span style={{ flex: 1, height: 6, borderRadius: 9, background: "rgba(255,255,255,0.07)", overflow: "hidden" }}>
                  <span style={{ display: "block", width: `${r.importance}%`, height: "100%",
                    background: r.essential ? "linear-gradient(90deg,#0033ff,#4a7cff)" : "rgba(255,255,255,0.22)" }} />
                </span>
                {pp && pp.premiumPct != null && (
                  <span title={`hedonic marginal pay premium (n=${pp.n})`} style={{ fontSize: 10, fontWeight: 700,
                    color: pp.premiumPct >= 0 ? "var(--good)" : "var(--t3)" }}>{pp.premiumPct >= 0 ? "+" : ""}{pp.premiumPct}%</span>
                )}
                {mom != null && (
                  <span title={`${a.metric} momentum · ${a.ecosystem}`} style={{ fontSize: 10, fontWeight: 700,
                    color: mom >= 0 ? "var(--good)" : "var(--bad)" }}>{mom >= 0 ? "▲" : "▼"}{Math.abs(mom)}%</span>
                )}
                {r.essential && <span className="tag" style={{ fontSize: 9 }}>core</span>}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  /* ---------------- Job Score board ---------------- */
  function ScoreBoard({ code, app, limit }) {
    const [open, setOpen] = useState(null);
    // sparse-mart safe: only roles that actually have this country×score (a real run
    // won't score every role in every country) — never crash on undefined.score.
    const has = r => r.countries && r.countries[code] && r.countries[code].score;
    const rows = [...S().roles].filter(has).sort((a, b) => b.countries[code].score.total - a.countries[code].score.total);
    const list = limit ? rows.slice(0, limit) : rows;
    return (
      <div className="col" style={{ gap: 4 }}>
        <div className="rank-row" style={{ padding: "0 18px 8px", cursor: "default" }}>
          <span className="eyebrow" style={{ fontSize: 10 }}>#</span>
          <span className="eyebrow" style={{ fontSize: 10 }}>Role</span>
          <span className="eyebrow" style={{ fontSize: 10 }}>Components</span>
          <span className="eyebrow" style={{ fontSize: 10, textAlign: "right" }}>Score</span>
          <span></span>
        </div>
        {list.map(r => {
          const sc = r.countries[code].score;
          const isOpen = open === r.id;
          return (
            <div key={r.id}>
              <div className={"rank-row" + (isOpen ? " open" : "")} onClick={() => setOpen(isOpen ? null : r.id)}>
                <span className={"rank-n" + (sc.rank <= 3 ? " top" : "")}>{sc.rank}</span>
                <span className="row gap10" style={{ minWidth: 0, alignItems: "center" }}>
                  <Uc.FamilyDot family={r.family} />
                  <span style={{ fontSize: 14, fontWeight: 600, color: "var(--t1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
                </span>
                <span className="row gap6" style={{ alignItems: "center" }}>
                  {[["D", sc.demand], ["P", sc.pay], ["O", sc.opp]].map(([k, v]) => (
                    <span key={k} className="row gap6" style={{ alignItems: "center", marginRight: 4 }}>
                      <span style={{ fontSize: 9, color: "var(--t3)", fontWeight: 700 }}>{k}</span>
                      <span style={{ width: 28, height: 4, borderRadius: 9, background: "rgba(255,255,255,0.08)", overflow: "hidden", display: "inline-block" }}>
                        <span style={{ display: "block", width: `${v * 10}%`, height: "100%", background: "linear-gradient(90deg,#0033ff,#4a7cff)" }} />
                      </span>
                    </span>
                  ))}
                </span>
                <span style={{ textAlign: "right" }}>
                  <span className="score-pill">{sc.total.toFixed(1)}</span>
                  <span className="pctile" style={{ display: "block" }}>top {sc.pctile}%</span>
                </span>
                <span style={{ color: "var(--t3)", textAlign: "center", transform: isOpen ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>▾</span>
              </div>
              {isOpen && (
                <div style={{ padding: "4px 18px 16px" }}>
                  <Uc.ScoreBreakdown score={sc} />
                  <div className="row between mt16 wrap-f gap10">
                    <span className="small" style={{ color: "var(--t3)" }}>
                      Composite of demand (42%), PPP-normalized pay (33%) and opportunity (25%). Derived from real data — not a fixed list.
                    </span>
                    <button className="pill sm solid" onClick={() => app.go("roles", { roleId: r.id })}>Open dashboard →</button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  /* ---------------- Roles index ---------------- */
  function RolesIndex({ app }) {
    const [q, setQ] = useState("");
    const [fam, setFam] = useState("all");
    const code = app.country;
    const filtered = S().roles.filter(r =>
      (fam === "all" || r.family.id === fam) &&
      (q === "" || r.name.toLowerCase().includes(q.toLowerCase()) || r.skills.some(s => s.name.toLowerCase().includes(q.toLowerCase()))));

    // never-dead-end: when the instant client filter misses, ask the backend
    // resolver (alias → fuzzy → embedding) for the nearest roles + honest copy.
    const [resolved, setResolved] = useState(null);
    const [resolving, setResolving] = useState(false);
    useEffect(() => {
      const ql = q.trim();
      if (ql === "" || filtered.length > 0) { setResolved(null); setResolving(false); return; }
      let alive = true;
      setResolving(true);
      const t = setTimeout(() => {
        resolveRoles(ql, 8)
          .then(r => { if (alive) { setResolved(r); setResolving(false); } })
          .catch(() => { if (alive) { setResolved(null); setResolving(false); } });
      }, 250);
      return () => { alive = false; clearTimeout(t); };
    }, [q, fam]);  // eslint-disable-line react-hooks/exhaustive-deps

    const byId = Object.fromEntries(S().roles.map(r => [r.id, r]));
    const showResolver = q.trim() !== "" && filtered.length === 0 &&
      resolved && resolved.results && resolved.results.length > 0;
    const displayRoles = showResolver
      ? resolved.results.map(rr => byId[rr.id]).filter(Boolean)
      : filtered;

    return (
      <div className="wrap surface-enter">
        <div className="sec-head" style={{ marginBottom: 28 }}>
          <div>
            <div className="sec-eyebrow">Roles</div>
            <div className="h1" style={{ color: "#fff" }}>Browse every tech role</div>
            <div className="body" style={{ marginTop: 8, maxWidth: 480 }}>Search a role to open its full dashboard — pay, trend, skills, ladder, demand and forecast, all traceable.</div>
          </div>
          <Uc.CountrySelect value={code} onChange={app.setCountry} />
        </div>

        <div className="row gap12 wrap-f roles-controls" style={{ marginBottom: 22 }}>
          <div style={{ position: "relative", flex: 1, minWidth: 240 }}>
            <span style={{ position: "absolute", left: 14, top: "50%", transform: "translateY(-50%)", color: "var(--t3)" }}>⌕</span>
            <input className="input" style={{ paddingLeft: 38 }} placeholder="Search roles or skills…" value={q} onChange={e => setQ(e.target.value)} />
          </div>
          <div className="seg">
            <button className={fam === "all" ? "on" : ""} onClick={() => setFam("all")}>All</button>
            {S().FAMILIES.map(f => <button key={f.id} className={fam === f.id ? "on" : ""} onClick={() => setFam(f.id)}>{f.name}</button>)}
          </div>
        </div>

        {/* Job Score board — hidden while searching so results surface immediately */}
        {q.trim() === "" ? (
          <div className="card" style={{ padding: "22px 10px", marginBottom: 32 }}>
            <div className="row between" style={{ padding: "0 12px 8px" }}>
              <div>
                <div className="card-title" style={{ fontSize: 16 }}>Job Score board · {S().C[code].name}</div>
                <div className="card-sub">Ranked by opportunity. Click any row to see how the score is built.</div>
              </div>
              {(() => {
                // honest board provenance: real aggregate sample across the roles present
                // in this country + a truthful "model composite" label — no fabricated figure.
                const present = S().roles.filter(r => r.countries && r.countries[code]);
                const totalSample = present.reduce((s, r) => s + (r.countries[code].sample || 0), 0);
                return <Uc.ConfidenceBadge align="right" data={{ conf: "high",
                  source: "Job Score composite (salary + demand + opportunity)", sample: totalSample,
                  kind: "model", freshness: present[0] ? present[0].countries[code].freshness : "—",
                  transparency: S().C[code].transparency }} />;
              })()}
            </div>
            <ScoreBoard code={code} app={app} limit={8} />
          </div>
        ) : (
          <div className="row between" style={{ marginBottom: 16 }}>
            <div className="small" style={{ color: "var(--t2)" }}>
              {showResolver ? (
                <span style={{ color: resolved.confidence === "low" ? "var(--t3)" : "var(--t2)" }}>{resolved.message}</span>
              ) : (
                <span><span style={{ color: "#fff", fontWeight: 700 }}>{filtered.length}</span> {filtered.length === 1 ? "role" : "roles"} matching <span style={{ color: "var(--sky)" }}>"{q}"</span></span>
              )}
            </div>
            <button className="pill sm ghost" onClick={() => setQ("")}>Clear search ×</button>
          </div>
        )}

        {/* role grid */}
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(280px,1fr))", gap: 16 }}>
          {displayRoles.map(r => {
            const cd = r.countries[code];
            return (
              <div key={r.id} className="card pooled lift role-card" onClick={(e) => window.openRoleMenu(r.id, e.clientX, e.clientY)}>
                <div className="row between">
                  <span className="row gap8" style={{ alignItems: "center" }}><Uc.FamilyDot family={r.family} /><span className="small" style={{ color: "var(--t3)" }}>{r.family.name}</span></span>
                  <span className="score-pill" style={{ fontSize: 15 }}>{cd.score.total.toFixed(1)}<span className="pctile" style={{ marginLeft: 5, fontSize: 10 }}>top {cd.score.pctile}%</span></span>
                </div>
                <div className="h3" style={{ color: "#fff" }}>{r.name}</div>
                <div className="small" style={{ color: "var(--t3)", flex: 1, lineHeight: 1.45 }}>{r.blurb}</div>
                <div className="row between" style={{ borderTop: "1px solid var(--line)", paddingTop: 14 }}>
                  <div><div className="tnum" style={{ fontSize: 18, fontWeight: 700, color: "#fff" }}>{S().fmtCur(cd.median, code)}</div><div style={{ fontSize: 10.5, color: "var(--t3)" }}>median · {code}</div></div>
                  <div className="row gap8"><span className="tag">demand {cd.demand}</span></div>
                </div>
              </div>
            );
          })}
        </div>
        {displayRoles.length === 0 && (
          resolving
            ? <Uc.Empty icon="⌕" title="Searching…" sub={`Finding the nearest roles to "${q}".`} />
            : <Uc.Empty icon="⌕" title="No roles match" sub="Try a different search or clear the family filter." />
        )}
      </div>
    );
  }

  /* ---------------- Role Dashboard (flagship) ---------------- */
  function StatCard({ children, style }) {
    return <div className="card pooled" style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", ...style }}>{children}</div>;
  }

  function RoleDashboard({ app, roleId }) {
    const [country, setCountry] = useState(app.country);
    const role = S().roleById(roleId);
    const cd = role.countries[country];
    const ui = UI;
    const isFav = app.favs.has("role:" + roleId);

    // short-series safe: a real 1-point series must not white-screen the dashboard.
    const ser = cd.series && cd.series.length ? cd.series : [{ year: 0, value: cd.median }];
    const prev = ser.length > 1 ? ser[ser.length - 2].value : ser[ser.length - 1].value;
    const deltaPct = (prev && cd.median != null) ? ((cd.median - prev) / prev) * 100 : 0;
    const baseIdx = Math.max(0, ser.length - 6);
    const baseVal = ser[baseIdx].value;
    const fiveYr = baseVal ? ser[ser.length - 1].value / baseVal - 1 : 0;

    return (
      <div className="wrap-wide surface-enter">
        {/* header */}
        <div className="row between wrap-f gap16" style={{ marginBottom: 26 }}>
          <div className="col gap10">
            <button className="pill ghost sm" style={{ alignSelf: "flex-start" }} onClick={() => app.back()}>← Back</button>
            <div className="row gap12" style={{ alignItems: "center", flexWrap: "wrap" }}>
              <ui.FamilyDot family={role.family} size={11} />
              <h1 className="h1" style={{ color: "#fff" }}>{role.name}</h1>
            </div>
            <div className="body" style={{ maxWidth: 560, marginTop: 2 }}>{role.blurb}</div>
            {role.lineage && role.lineage.length > 0 && (
              <div className="small" style={{ color: "var(--t3)", maxWidth: 560, marginTop: 2 }}
                title="A discovered role — clustered from these member titles, not curated.">
                <span style={{ color: "var(--t2)", fontWeight: 600 }}>Clustered from</span>{" "}
                {role.lineage.slice(0, 6).join(" · ")}
                {role.lineage.length > 6 && <span> +{role.lineage.length - 6} more</span>}
              </div>
            )}
          </div>
          <div className="col gap10" style={{ alignItems: "flex-end" }}>
            <div className="row gap8">
              <button className={"iconbtn" + (isFav ? " on" : "")} onClick={() => app.toggleFav("role", roleId, role.name)} title="Save">{isFav ? "★" : "☆"}</button>
              <button className="pill sm" onClick={() => app.addTray("role", roleId)}>+ Compare</button>
            </div>
            <ui.CountrySelect value={country} onChange={setCountry} />
          </div>
        </div>

        {/* hero stat row */}
        <div className="grid" style={{ gridTemplateColumns: "1.5fr 1fr 1.1fr", gap: 16, marginBottom: 16 }}>
          <StatCard>
            <div className="row between"><span className="stat-label">Median salary · {S().C[country].name}</span><ui.ConfidenceBadge data={cd} align="right" /></div>
            <div className="mt16"><ui.BigSalary value={cd.median} code={country} size={46} /></div>
            <div className="row gap12 mt12" style={{ alignItems: "center" }}>
              <span className={"delta " + (deltaPct >= 0 ? "up" : "down")}>{deltaPct >= 0 ? "↑" : "↓"} {Math.abs(deltaPct).toFixed(1)}% YoY</span>
              <span className="small" style={{ color: "var(--t3)" }}>{(fiveYr * 100).toFixed(0)}% over 5 yrs · {cd.kind === "job-level" ? "job-level median" : "person-level"}</span>
            </div>
            <SalaryLenses lenses={cd.salaryLenses} code={country} />
          </StatCard>
          <StatCard>
            <span className="stat-label">Job Score</span>
            <div className="row gap10 mt16" style={{ alignItems: "baseline" }}>
              <span className="tnum" style={{ fontSize: 46, fontWeight: 700, color: "#fff", lineHeight: 1 }}>{cd.score.total.toFixed(1)}</span>
              <span style={{ color: "var(--t3)", fontSize: 15 }}>/10</span>
            </div>
            <div className="row between mt12"><span className="pctile" style={{ fontSize: 13 }}>top {cd.score.pctile}% of roles</span><span className="small" style={{ color: "var(--t3)" }}>rank #{cd.score.rank}</span></div>
          </StatCard>
          <StatCard>
            <span className="stat-label">Demand vs Interest</span>
            <div className="col gap12 mt16">
              <DemandInterestGauge label="Market demand" value={cd.demand} color="#4a7cff"
                note={cd.postings != null ? `${cd.postings.toLocaleString()} postings` : null} />
              <DemandInterestGauge label="Learner interest" value={cd.interest} color="#ffcc4d" />
            </div>
            <div className="small mt12" style={{ color: cd.demand > cd.interest ? "var(--good)" : "var(--t3)" }}>
              {cd.demand > cd.interest ? `Demand outpaces interest by ${cd.demand - cd.interest} pts — an opening.` : `More pursuing than the market wants right now.`}
            </div>
            {cd.outlook && (
              <div className="small mt8" style={{ color: "var(--t2)" }}>
                {cd.outlook.horizon}-yr official outlook{" "}
                <span style={{ color: cd.outlook.growthPct >= 0 ? "var(--good)" : "var(--bad)", fontWeight: 700 }}>
                  {cd.outlook.growthPct >= 0 ? "▲" : "▼"} {cd.outlook.growthPct >= 0 ? "+" : ""}{cd.outlook.growthPct}%
                </span>
                {cd.outlook.shortage ? <span style={{ color: "var(--warn)" }}> · {cd.outlook.shortage}</span> : null}
                <span style={{ color: "var(--t3)" }}> · {cd.outlook.source}</span>
              </div>
            )}
          </StatCard>
        </div>

        {/* row 2: salary trend (wide) + skills */}
        <div className="grid" style={{ gridTemplateColumns: "1.6fr 1fr", gap: 16, marginBottom: 16 }}>
          <div className="card pooled">
            <div className="card-head"><div><div className="card-title">Salary over time</div><div className="card-sub">Median {role.name} pay · {S().C[country].name} · native currency</div></div>
              <ui.ConfidenceBadge data={cd} align="right" /></div>
            <Charts.SalaryTrend series={cd.series} code={country} height={220} />
          </div>
          <div className="card">
            <div className="card-head"><div><div className="card-title">Skills & durability</div><div className="card-sub">Required level · long-term durability</div></div></div>
            <div className="row gap16 mb8" style={{ fontSize: 10.5, color: "var(--t3)", marginBottom: 6 }}>
              <span className="row gap6" style={{ alignItems: "center" }}><span style={{ width: 8, height: 8, borderRadius: 9, background: "var(--good)", display: "inline-block" }}></span>durable</span>
              <span className="row gap6" style={{ alignItems: "center" }}><span style={{ width: 8, height: 8, borderRadius: 9, background: "var(--warn)", display: "inline-block" }}></span>watch</span>
              <span className="row gap6" style={{ alignItems: "center" }}><span style={{ width: 8, height: 8, borderRadius: 9, background: "var(--bad)", display: "inline-block" }}></span>fading</span>
            </div>
            <div className="col">
              {role.skills.map(s => <ui.SkillRow key={s.name} skill={s} />)}
            </div>
          </div>
        </div>

        {/* row 3: demand trajectory + forecast (distinct) | ladder */}
        <div className="grid" style={{ gridTemplateColumns: "1.6fr 1fr", gap: 16, marginBottom: 16 }}>
          <div className="card pooled">
            <div className="card-head"><div><div className="card-title">Demand trajectory & forecast</div><div className="card-sub">Indexed demand 2017–2025, projected to 2028 with confidence band</div></div>
              <ui.ConfidenceBadge data={{ ...cd, conf: "med", source: "Demand model + posting volume" }} align="right" /></div>
            <Charts.ForecastChart history={cd.demandSeries} forecast={cd.forecast} height={210} />
            <div className="small mt12" style={{ color: "var(--t3)" }}>Dashed line is projected, not observed. The band widens with uncertainty — we don't pretend to know 2028 precisely.</div>
          </div>
          <div className="card">
            <div className="card-head"><div><div className="card-title">Role progression</div>
              <div className="card-sub">{role.payLadder && role.payLadder.length
                ? "Real pay by level · US H-1B disclosed, employers pooled"
                : "How pay steps along the ladder"}</div></div></div>
            <div className="col" style={{ gap: 2 }}>
              {role.payLadder && role.payLadder.length ? (
                role.payLadder.map((rung, i) => (
                  <div key={i} className="row gap12" style={{ alignItems: "center", padding: "10px 0", borderBottom: i < role.payLadder.length - 1 ? "1px solid var(--line)" : "none" }}>
                    <span style={{ width: 22, height: 22, borderRadius: 999, flexShrink: 0, display: "grid", placeItems: "center", fontSize: 11, fontWeight: 700,
                      background: "rgba(255,255,255,0.06)", color: "var(--t3)" }}>{i + 1}</span>
                    <span style={{ flex: 1, fontSize: 13, color: "var(--t1)" }}>{rung.label}
                      <span className="small" style={{ color: "var(--t3)", marginLeft: 6 }}>n={rung.n.toLocaleString()}</span>
                      {rung.stepPct != null && <span className="small" style={{ color: "var(--good)", marginLeft: 6 }}>▲ +{rung.stepPct}%</span>}
                    </span>
                    <span className="tnum" style={{ fontSize: 13.5, fontWeight: 700, color: "#fff" }}>${rung.median.toLocaleString()}</span>
                  </div>
                ))
              ) : (
                role.ladder.map(([title, mult], i) => {
                  const val = Math.round(cd.median * mult);
                  const isHere = Math.abs(mult - 1) < 0.001;
                  return (
                    <div key={i} className="row gap12" style={{ alignItems: "center", padding: "10px 0", borderBottom: i < role.ladder.length - 1 ? "1px solid var(--line)" : "none" }}>
                      <span style={{ width: 22, height: 22, borderRadius: 999, flexShrink: 0, display: "grid", placeItems: "center", fontSize: 11, fontWeight: 700,
                        background: isHere ? "var(--cobalt)" : "rgba(255,255,255,0.06)", color: isHere ? "#fff" : "var(--t3)",
                        boxShadow: isHere ? "0 0 14px rgba(0,51,255,0.6)" : "none" }}>{i + 1}</span>
                      <span style={{ flex: 1, fontSize: 13.5, color: isHere ? "#fff" : "var(--t1)", fontWeight: isHere ? 700 : 500 }}>{title}{isHere && <span className="tag" style={{ marginLeft: 8, fontSize: 9 }}>this role</span>}</span>
                      <span className="tnum" style={{ fontSize: 13.5, fontWeight: 700, color: "#fff" }}>{S().fmtCompact(val, country)}</span>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>

        {/* row 4: cross-country strip */}
        <div className="card pooled">
          <div className="card-head"><div><div className="card-title">{role.name} across all 7 countries</div><div className="card-sub">Median pay · {app.ppp ? "PPP-adjusted (international $)" : "native currency, not converted"}</div></div>
            <div className="row gap10">
              <button className="pill sm ghost" onClick={() => app.setPpp(!app.ppp)}>{app.ppp ? "PPP ✓" : "Nominal"}</button>
              <button className="pill sm" onClick={() => app.go("compare", { mode: "country", roleId })}>Open in Compare →</button>
            </div></div>
          <CrossCountryStrip role={role} ppp={app.ppp} highlight={country} onPick={setCountry} />
        </div>

        {/* row 5: trajectory (where this role leads) + importance-weighted skills */}
        {((role.trajectory && role.trajectory.length) || (role.importance && role.importance.length)) ? (
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
            <Trajectory role={role} app={app} />
            <ImportanceSkills role={role} />
          </div>
        ) : null}
        <div style={{ height: 40 }}></div>
      </div>
    );
  }

  function DemandInterestGauge({ label, value, color, note }) {
    return (
      <div>
        <div className="row between" style={{ marginBottom: 5 }}>
          <span className="small" style={{ color: "var(--t2)" }}>{label}</span>
          <span className="row gap8" style={{ alignItems: "baseline" }}>
            {note && <span className="small tnum" style={{ color: "var(--t3)", fontSize: 11 }}>{note}</span>}
            <span className="tnum" style={{ fontSize: 13, fontWeight: 700, color: "#fff" }}>{value}</span>
          </span>
        </div>
        <div style={{ height: 6, borderRadius: 9, background: "rgba(255,255,255,0.07)", overflow: "hidden" }}>
          <div style={{ width: `${value}%`, height: "100%", background: color, boxShadow: `0 0 10px ${color}`, transition: "width 0.7s" }} />
        </div>
      </div>
    );
  }

  function CrossCountryStrip({ role, ppp, highlight, onPick }) {
    const items = S().COUNTRIES.map(co => {
      const m = role.countries[co.code].median;
      const pppv = S().pppUSD(m, co.code);
      // bar geometry ALWAYS by PPP so cross-currency lengths are fair;
      // label shows native nominal (or ◊PPP when toggled).
      return { code: co.code, name: co.name, native: m, ppp: pppv, val: ppp ? pppv : m, geom: pppv };
    });
    const mx = Math.max(...items.map(i => i.geom));
    return (
      <div className="col" style={{ gap: 10 }}>
        {items.sort((a, b) => b.geom - a.geom).map(it => (
          <button key={it.code} onClick={() => onPick(it.code)} className="row gap12" style={{
            alignItems: "center", background: it.code === highlight ? "rgba(42,91,255,0.08)" : "transparent",
            border: "1px solid " + (it.code === highlight ? "rgba(74,124,255,0.3)" : "transparent"), borderRadius: 10,
            padding: "8px 10px", cursor: "pointer", width: "100%", textAlign: "left"
          }}>
            <span style={{ width: 150 }}><UI.CountryTag code={it.code} /></span>
            <div style={{ flex: 1, height: 20, background: "rgba(255,255,255,0.05)", borderRadius: 6, overflow: "hidden" }}>
              <div style={{ width: `${(it.geom / mx) * 100}%`, height: "100%", borderRadius: 6, background: it.code === highlight ? "linear-gradient(90deg,#0033ff,#7aa0ff)" : "linear-gradient(90deg,#22397f,#3f64c4)", transition: "width 0.7s" }} />
            </div>
            <span className="tnum" style={{ width: 120, textAlign: "right", fontSize: 13.5, fontWeight: 700, color: "#fff" }}>
              {ppp ? "◊" + Math.round(it.ppp / 1000) + "k" : S().fmtCompact(it.native, it.code)}
            </span>
          </button>
        ))}
        <div className="small" style={{ color: "var(--t3)", marginTop: 4 }}>
          {ppp
            ? "◊ = international dollars (purchasing-power parity). No live FX conversion."
            : "Bars sized by purchasing power so markets are comparable; figures shown in each country's own currency."}
        </div>
      </div>
    );
  }

  function Roles({ app }) {
    if (app.route.roleId) return <RoleDashboard app={app} roleId={app.route.roleId} />;
    return <RolesIndex app={app} />;
  }

export { Roles, ScoreBoard };
