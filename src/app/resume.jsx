import React from "react";
import { STRATA } from "../data/mock.js";
import { UI } from "./ui.jsx";
import { Charts } from "./charts.jsx";
/* ============================================================
   strata — Resume surface
   ============================================================ */
  const { useState } = React;
  const S = () => STRATA;
  const Uc = UI, Cc = Charts;

  // profile market value: weighted blend of matched roles' medians, scaled by experience
  function profileValue(profile, code) {
    const roles = profile.matchRoles.map(id => S().roleById(id));
    const weights = roles.map((_, i) => 1 / (i + 1.4));
    const wsum = weights.reduce((a, b) => a + b, 0);
    const base = roles.reduce((acc, r, i) => acc + r.countries[code].median * weights[i], 0) / wsum;
    const expFactor = 0.82 + Math.min(0.4, profile.years * 0.05);
    const v = base * expFactor;
    return code === "IN" ? Math.round(v / 50000) * 50000 : v > 200000 ? Math.round(v / 5000) * 5000 : Math.round(v / 1000) * 1000;
  }
  const matchList = (profile, n) => profile.matchRoles.map(id => S().roleById(id)).slice(0, n || 5);
  const fitPct = (profile, r, i) => Math.min(98, Math.round(92 - i * 9 + profile.skills.filter(s => r.skills.some(rs => rs.name === s)).length));
  const learnNext = profile => [...new Set(matchList(profile).flatMap(r => r.skills).filter(s => s.trend === "rising" && s.dura >= 70 && !profile.skills.includes(s.name)).map(s => s.name))].slice(0, 4);

  function Dropzone({ onParse }) {
    const [hot, setHot] = useState(false);
    return (
      <div className="card pooled" style={{ maxWidth: 640, margin: "0 auto", padding: 0 }}>
        <div className={"dropzone" + (hot ? " hot" : "")}
          onDragOver={e => { e.preventDefault(); setHot(true); }} onDragLeave={() => setHot(false)}
          onDrop={e => { e.preventDefault(); setHot(false); onParse(); }} onClick={onParse}>
          <div style={{ fontSize: 34, marginBottom: 14 }}>⤓</div>
          <div className="h3" style={{ color: "var(--white)" }}>Drop a résumé to price your profile</div>
          <div className="body" style={{ maxWidth: 420, margin: "10px auto 0" }}>PDF or DOCX — a 5-second action. We parse skills and experience, then value the whole profile per country. Nothing required, nothing stored.</div>
          <div className="row gap10 center mt24"><span className="pill solid">Choose file</span><span className="pill ghost">or use a sample profile</span></div>
        </div>
      </div>
    );
  }

  // ---- a full, self-contained profile read-out (used for A and for B) ----
  function ProfilePanel({ profile, country, accent, app, badge }) {
    const val = profileValue(profile, country);
    const matches = matchList(profile, 4);
    const target = S().roleById(profile.matchRoles[0]);
    const gap = target.skills.filter(s => !profile.skills.includes(s.name)).slice(0, 4);
    const learn = learnNext(profile);
    return (
      <div className="card pooled" style={{ borderTop: `2px solid ${accent}` }}>
        <div className="row between" style={{ marginBottom: 16 }}>
          <div className="row gap12" style={{ alignItems: "center" }}>
            <div style={{ width: 40, height: 40, borderRadius: 11, background: accent + "28", display: "grid", placeItems: "center", fontSize: 15, border: `1px solid ${accent}55`, color: "var(--white)", fontWeight: 800 }}>{badge}</div>
            <div><div className="h3" style={{ color: "var(--white)", fontSize: 18 }}>{profile.title}</div><div className="small" style={{ color: "var(--t3)" }}>{profile.years} yrs · {profile.skills.length} skills</div></div>
          </div>
          <span style={{ width: 9, height: 9, borderRadius: 9, background: accent, boxShadow: `0 0 10px ${accent}` }}></span>
        </div>

        {/* value */}
        <div style={{ padding: "14px 0", borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }}>
          <div className="row between" style={{ alignItems: "flex-end" }}>
            <div><span className="stat-label">Market value · {country}</span><div className="mt8"><Uc.BigSalary value={val} code={country} size={36} /></div></div>
            <Uc.ConfidenceBadge data={{ conf: "med", source: "Profile model · matched roles", sample: 2400, kind: "person-level", freshness: "live", transparency: S().C[country].transparency }} align="right" />
          </div>
        </div>

        {/* role matches */}
        <div className="card-sub" style={{ margin: "16px 0 10px" }}>ROLE MATCHES</div>
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          {matches.map((r, i) => (
            <button key={r.id} onClick={(e) => window.openRoleMenu(r.id, e.clientX, e.clientY)} className="row between" style={{ background: "var(--wash)", border: "1px solid var(--line)", borderRadius: 10, padding: "9px 11px", cursor: "pointer", textAlign: "left" }}>
              <span className="row gap8" style={{ minWidth: 0, alignItems: "center" }}><Uc.FamilyDot family={r.family} /><span style={{ fontSize: 12.5, color: "var(--t1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span></span>
              <span className="pctile" style={{ flexShrink: 0 }}>{fitPct(profile, r, i)}%</span>
            </button>
          ))}
        </div>

        {/* recommendation */}
        <div className="card-sub" style={{ margin: "18px 0 8px" }}>SKILLS-GAP → {target.name.toUpperCase()}</div>
        {gap.length ? gap.map(s => (
          <div key={s.name} className="row between" style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
            <span className="row gap10"><span style={{ fontSize: 13, color: "var(--t1)" }}>{s.name}</span><Uc.ProfTag level={s.level} /></span>
            <Cc.Durability value={s.dura} width={54} />
          </div>
        )) : <div className="small" style={{ color: "var(--good)" }}>Covers the core skills of its top match.</div>}

        {/* learn next */}
        <div className="card-sub" style={{ margin: "16px 0 8px" }}>LEARN NEXT</div>
        <div className="chips">{learn.length ? learn.map(n => <span key={n} className="pill sm" style={{ background: accent + "22", borderColor: accent + "55", color: "var(--white)" }}>{n} ↗</span>) : <span className="small" style={{ color: "var(--t3)" }}>Already future-aligned.</span>}</div>
      </div>
    );
  }

  // ---- deep comparison between two profiles ----
  function DeepCompare({ a, b, country }) {
    const COL_A = "#6FA2FF", COL_B = "#E8B765";
    const axes = a.axes.map(x => x.axis);
    const radarSeries = [
      { label: a.title, color: COL_A, values: a.axes.map(x => x.you) },
      { label: b.title, color: COL_B, values: b.axes.map((x, i) => x.you) },
    ];
    const valsA = S().COUNTRIES.map(co => ({ code: co.code, native: profileValue(a, co.code), ppp: S().pppUSD(profileValue(a, co.code), co.code) }));
    const valsB = S().COUNTRIES.map(co => ({ code: co.code, native: profileValue(b, co.code), ppp: S().pppUSD(profileValue(b, co.code), co.code) }));
    const mxPPP = Math.max(...valsA.map(x => x.ppp), ...valsB.map(x => x.ppp));
    const onlyA = a.skills.filter(s => !b.skills.includes(s));
    const onlyB = b.skills.filter(s => !a.skills.includes(s));
    const shared = a.skills.filter(s => b.skills.includes(s));
    const vA = profileValue(a, country), vB = profileValue(b, country);
    const leader = vA >= vB ? a : b;
    const sep = a.axes.map((x, i) => ({ axis: x.axis, d: Math.abs(x.you - b.axes[i].you) })).sort((p, q) => q.d - p.d).slice(0, 2).map(x => x.axis);

    return (
      <div className="grid" style={{ gap: 16 }}>
        <div className="card" style={{ background: "rgba(111,162,255,0.1)" }}>
          <div className="row between wrap-f gap16" style={{ alignItems: "center" }}>
            <div>
              <div className="sec-eyebrow">Verdict · {S().C[country].name}</div>
              <div className="h3" style={{ color: "var(--white)", marginTop: 4 }}>
                <span style={{ color: leader === a ? COL_A : COL_B }}>{leader.title}</span> prices {Math.round(Math.abs(vA - vB) / Math.min(vA, vB) * 100)}% higher
              </div>
              <div className="body" style={{ marginTop: 6, maxWidth: 560 }}>The two profiles separate most on <strong style={{ color: "var(--white)" }}>{sep.join(" and ")}</strong>. {leader.title} leads on raw value here; the gap narrows in markets where the other profile's strengths are scarcer.</div>
            </div>
            <div className="row gap20">
              {[[a, vA, COL_A], [b, vB, COL_B]].map(([p, v, c]) => (
                <div key={p.title} style={{ textAlign: "right" }}>
                  <div className="row gap8" style={{ justifyContent: "flex-end", alignItems: "center" }}><span style={{ width: 8, height: 8, borderRadius: 9, background: c, display: "inline-block" }}></span><span className="small" style={{ color: "var(--t3)" }}>{p.title}</span></div>
                  <div className="tnum" style={{ fontSize: 26, fontWeight: 700, color: "var(--white)", marginTop: 2 }}>{S().fmtCompact(v, country)}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="grid" style={{ gridTemplateColumns: "1fr 1.2fr", gap: 16 }}>
          <div className="card pooled">
            <div className="card-head"><div><div className="card-title">Profile shape</div><div className="card-sub">Strengths overlaid</div></div></div>
            <div style={{ display: "grid", placeItems: "center", paddingTop: 4 }}><Cc.RadarMulti axes={axes} series={radarSeries} size={300} /></div>
            <div className="row gap20 center mt12">
              <span className="row gap6" style={{ alignItems: "center", fontSize: 12, color: "var(--t2)" }}><span style={{ width: 14, height: 3, background: COL_A, borderRadius: 2, display: "inline-block" }}></span>{a.title}</span>
              <span className="row gap6" style={{ alignItems: "center", fontSize: 12, color: "var(--t2)" }}><span style={{ width: 14, height: 3, background: COL_B, borderRadius: 2, display: "inline-block" }}></span>{b.title}</span>
            </div>
          </div>
          <div className="card pooled">
            <div className="card-head"><div><div className="card-title">Value across all 7 markets</div><div className="card-sub">Native currency · bars sized by purchasing power</div></div></div>
            <div className="col" style={{ gap: 12 }}>
              {S().COUNTRIES.map((co, i) => (
                <div key={co.code} className="row gap12" style={{ alignItems: "center" }}>
                  <span style={{ width: 116 }}><Uc.CountryTag code={co.code} sm /></span>
                  <div style={{ flex: 1 }}>
                    <div style={{ height: 9, background: "var(--wash)", borderRadius: 6, overflow: "hidden", marginBottom: 4 }}><div style={{ width: `${(valsA[i].ppp / mxPPP) * 100}%`, height: "100%", borderRadius: 6, background: COL_A }} /></div>
                    <div style={{ height: 9, background: "var(--wash)", borderRadius: 6, overflow: "hidden" }}><div style={{ width: `${(valsB[i].ppp / mxPPP) * 100}%`, height: "100%", borderRadius: 6, background: COL_B }} /></div>
                  </div>
                  <span className="tnum col" style={{ width: 92, textAlign: "right", fontSize: 11.5, fontWeight: 700, lineHeight: 1.45 }}>
                    <span style={{ color: COL_A }}>{S().fmtCompact(valsA[i].native, co.code)}</span>
                    <span style={{ color: COL_B }}>{S().fmtCompact(valsB[i].native, co.code)}</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head"><div><div className="card-title">What separates them</div><div className="card-sub">{shared.length} shared skills · the rest is differentiation</div></div></div>
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
            <div>
              <div className="row gap8 mb8" style={{ alignItems: "center", marginBottom: 10 }}><span style={{ width: 8, height: 8, borderRadius: 9, background: COL_A, display: "inline-block" }}></span><span className="small" style={{ color: "var(--t2)" }}>Only {a.title}</span></div>
              <div className="chips">{onlyA.length ? onlyA.map(s => <span key={s} className="tag" style={{ color: "#6FA2FF", borderColor: COL_A + "55" }}>{s}</span>) : <span className="small" style={{ color: "var(--t3)" }}>—</span>}</div>
            </div>
            <div>
              <div className="row gap8" style={{ alignItems: "center", marginBottom: 10 }}><span style={{ width: 8, height: 8, borderRadius: 9, background: "var(--t3)", display: "inline-block" }}></span><span className="small" style={{ color: "var(--t2)" }}>Shared</span></div>
              <div className="chips">{shared.map(s => <span key={s} className="tag">{s}</span>)}</div>
            </div>
            <div>
              <div className="row gap8" style={{ alignItems: "center", marginBottom: 10 }}><span style={{ width: 8, height: 8, borderRadius: 9, background: COL_B, display: "inline-block" }}></span><span className="small" style={{ color: "var(--t2)" }}>Only {b.title}</span></div>
              <div className="chips">{onlyB.length ? onlyB.map(s => <span key={s} className="tag" style={{ color: "#E8B765", borderColor: COL_B + "55" }}>{s}</span>) : <span className="small" style={{ color: "var(--t3)" }}>—</span>}</div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  function ResumeResults({ app, profile }) {
    const [country, setCountry] = useState(app.country);
    const [target, setTarget] = useState("ml-eng");
    const [compareB, setCompareB] = useState(false);
    const profileB = S().RESUME_B;
    const val = profileValue(profile, country);
    const matches = matchList(profile, 5);
    const tgt = S().roleById(target);
    const gap = tgt.skills.filter(s => !profile.skills.includes(s.name));
    const learn = learnNext(profile);
    const best = S().COUNTRIES.map(co => ({ code: co.code, ppp: S().pppUSD(profileValue(profile, co.code), co.code), native: profileValue(profile, co.code) })).sort((a, b) => b.ppp - a.ppp);
    const mxBest = Math.max(...best.map(b => b.ppp));

    const header = (
      <div className="row between wrap-f gap12" style={{ marginBottom: 24 }}>
        <div className="row gap12" style={{ alignItems: "center" }}>
          <div style={{ width: 46, height: 46, borderRadius: 12, background: "rgba(111,162,255,0.14)", display: "grid", placeItems: "center", fontSize: 18, border: "1px solid var(--glass-line)" }}>◑</div>
          <div><div className="h3" style={{ color: "var(--white)" }}>{compareB ? "Résumé A vs B" : profile.title}</div><div className="small" style={{ color: "var(--t3)" }}>{compareB ? `${profile.title} vs ${profileB.title}` : `${profile.years} yrs experience · ${profile.skills.length} skills parsed`}</div></div>
        </div>
        <div className="row gap10">
          {compareB && <button className="pill sm ghost" onClick={() => setCompareB(false)}>← Single profile</button>}
          <button className="pill sm ghost" onClick={() => app.go("resume", { reset: true })}>↻ New résumé</button>
          <Uc.CountrySelect value={country} onChange={setCountry} />
        </div>
      </div>
    );

    if (compareB) {
      return (
        <div className="wrap-wide">
          {header}
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
            <ProfilePanel profile={profile} country={country} accent="#6FA2FF" app={app} badge="A" />
            <ProfilePanel profile={profileB} country={country} accent="#E8B765" app={app} badge="B" />
          </div>
          <div className="sec-head" style={{ margin: "30px 0 18px" }}><div><div className="sec-eyebrow">Head to head</div><div className="h2" style={{ color: "var(--white)" }}>Where they diverge</div></div></div>
          <DeepCompare a={profile} b={profileB} country={country} />
          <div style={{ height: 40 }}></div>
        </div>
      );
    }

    return (
      <div className="wrap-wide">
        {header}

        {/* market value + best market */}
        <div className="grid" style={{ gridTemplateColumns: "1fr 1.3fr", gap: 16, marginBottom: 16 }}>
          <div className="card pooled spotlit">
            <span className="stat-label">Your market value · {S().C[country].name}</span>
            <div className="mt16"><Uc.BigSalary value={val} code={country} size={48} /></div>
            <div className="small mt12" style={{ color: "var(--t2)" }}>Whole-profile valuation, blended across your strongest role matches and scaled to {profile.years} years. Per country, in native currency.</div>
            <div className="mt16"><Uc.ConfidenceBadge data={{ conf: "med", source: "Profile model · matched roles", sample: 2400, kind: "person-level", freshness: "live", transparency: S().C[country].transparency }} /></div>
          </div>
          <div className="card">
            <div className="card-head"><div><div className="card-title">Best market for your profile</div><div className="card-sub">Ranked by purchasing power (PPP)</div></div></div>
            <div className="col" style={{ gap: 9 }}>
              {best.map(it => (
                <button key={it.code} onClick={() => setCountry(it.code)} className="row gap12" style={{ alignItems: "center", background: it.code === country ? "rgba(111,162,255,0.1)" : "transparent", border: "1px solid " + (it.code === country ? "rgba(111,162,255,0.5)" : "transparent"), borderRadius: 9, padding: "6px 9px", cursor: "pointer", width: "100%", textAlign: "left" }}>
                  <span style={{ width: 140 }}><Uc.CountryTag code={it.code} sm /></span>
                  <div style={{ flex: 1, height: 16, background: "var(--wash)", borderRadius: 5, overflow: "hidden" }}><div style={{ width: `${(it.ppp / mxBest) * 100}%`, height: "100%", borderRadius: 5, background: "var(--bar)" }} /></div>
                  <span className="tnum" style={{ width: 96, textAlign: "right", fontSize: 13, fontWeight: 700, color: "var(--white)" }}>{S().fmtCompact(it.native, it.code)}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* role matches */}
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head"><div><div className="card-title">Role matches</div><div className="card-sub">Roles your profile fits — click to open or compare</div></div></div>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))", gap: 12 }}>
            {matches.map((r, i) => (
              <div key={r.id} className="card tight lift" style={{ cursor: "pointer" }} onClick={(e) => window.openRoleMenu(r.id, e.clientX, e.clientY)}>
                <div className="row between" style={{ marginBottom: 10 }}><Uc.FamilyDot family={r.family} /><span className="pctile">{fitPct(profile, r, i)}% fit</span></div>
                <div style={{ fontSize: 14.5, fontWeight: 600, color: "var(--white)" }}>{r.name}</div>
                <div className="tnum mt8" style={{ fontSize: 16, fontWeight: 700, color: "var(--white)" }}>{S().fmtCur(r.countries[country].median, country)}</div>
                <div className="row gap8 mt12"><span className="tag" style={{ fontSize: 10 }}>demand {r.countries[country].demand}</span><span className="tag" style={{ fontSize: 10 }}>★ {r.countries[country].score.total.toFixed(1)}</span></div>
              </div>
            ))}
          </div>
        </div>

        {/* recommendation engine */}
        <div className="grid" style={{ gridTemplateColumns: "1.2fr 1fr", gap: 16, marginBottom: 16 }}>
          <div className="card pooled">
            <div className="card-head"><div><div className="card-title">Recommendation engine</div><div className="card-sub">Skills-gap to a target role</div></div>
              <ResumeTargetPicker value={target} onChange={setTarget} /></div>
            <div className="small" style={{ color: "var(--t2)", marginBottom: 14 }}>To move toward <strong style={{ color: "var(--white)" }}>{tgt.name}</strong>, close these gaps:</div>
            {gap.length ? (
              <div className="col">
                {gap.map(s => (
                  <div key={s.name} className="row between" style={{ padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
                    <span className="row gap10"><span style={{ fontSize: 14, color: "var(--t1)" }}>{s.name}</span><Uc.ProfTag level={s.level} /></span>
                    <span className="row gap10" style={{ alignItems: "center" }}><span style={{ fontSize: 11, color: s.trend === "rising" ? "var(--good)" : "var(--t3)" }}>{s.trend}</span><Cc.Durability value={s.dura} /></span>
                  </div>
                ))}
              </div>
            ) : <div className="small" style={{ color: "var(--good)" }}>You already cover this role's core skills.</div>}
          </div>
          <div className="card">
            <div className="card-title" style={{ marginBottom: 6 }}>What to learn next</div>
            <div className="card-sub" style={{ marginBottom: 16 }}>Rising demand × high durability, missing from your profile</div>
            <div className="chips">{learn.length ? learn.map(n => <span key={n} className="pill sm active">{n} ↗</span>) : <span className="small" style={{ color: "var(--t3)" }}>Your skills are already future-aligned.</span>}</div>
            <div className="divider"></div>
            <div className="card-title" style={{ marginBottom: 12 }}>Adjacent roles</div>
            <div className="col gap8">
              {matches.slice(1, 4).map(r => (
                <button key={r.id} onClick={(e) => window.openRoleMenu(r.id, e.clientX, e.clientY)} className="row between" style={{ background: "transparent", border: "none", cursor: "pointer", padding: "6px 0", width: "100%" }}>
                  <span className="row gap8" style={{ color: "var(--t1)", fontSize: 13.5 }}><Uc.FamilyDot family={r.family} />{r.name}</span>
                  <span style={{ color: "var(--sky)", fontSize: 12 }}>→</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* A vs B — opt-in, never fabricated */}
        <div className="card" style={{ textAlign: "center", padding: "34px 24px" }}>
          <div className="h3" style={{ color: "var(--white)" }}>Compare against a second résumé</div>
          <div className="body" style={{ maxWidth: 460, margin: "10px auto 0" }}>Add another profile to see both priced fully, side by side — then a head-to-head on shape, markets, and the skills that separate them.</div>
          <div className="row gap10 center mt24">
            <button className="pill solid" onClick={() => setCompareB(true)}>+ Add résumé B</button>
            <button className="pill ghost" onClick={() => setCompareB(true)}>Use a sample profile</button>
          </div>
        </div>
        <div style={{ height: 40 }}></div>
      </div>
    );
  }

  function ResumeTargetPicker({ value, onChange }) {
    return <select className="pill" style={{ appearance: "none", paddingRight: 26 }} value={value} onChange={e => onChange(e.target.value)}>
      {S().roles.map(r => <option key={r.id} value={r.id} style={{ background: "var(--ink-2)", color: "var(--white)" }}>{r.name}</option>)}
    </select>;
  }

  function Resume({ app }) {
    const [parsed, setParsed] = useState(false);
    React.useEffect(() => { if (app.route.reset) { setParsed(false); } }, [app.route.reset]);
    return (
      <div className="wrap-wide surface-enter">
        <div className="sec-head" style={{ marginBottom: 30 }}>
          <div><div className="sec-eyebrow">Résumé</div><div className="h1" style={{ color: "var(--white)" }}>What's your profile worth?</div>
            <div className="body" style={{ marginTop: 8, maxWidth: 540 }}>Drop a résumé and get a whole-profile valuation per country, role matches, a skills-gap plan, and an in-depth head-to-head against another profile.</div></div>
        </div>
        {!parsed ? <Dropzone onParse={() => setParsed(true)} /> : <ResumeResults app={app} profile={S().RESUME_SAMPLE} />}
      </div>
    );
  }

export { Resume };
