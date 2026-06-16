import React from "react";
import { STRATA } from "../data/mock.js";
import { UI } from "./ui.jsx";
import { Charts } from "./charts.jsx";
import { Roles } from "./roles.jsx";
import { Compare } from "./compare.jsx";
import { Resume } from "./resume.jsx";
import { Countries } from "./countries.jsx";
/* ============================================================
   strata — mobile app (reuses data/charts/ui + desktop surfaces)
   ============================================================ */
  const { useState, useEffect, useRef } = React;
  const S = () => STRATA;

  // ---- tab icons (simple geometric) ----
  const Icon = ({ name }) => {
    const p = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" };
    switch (name) {
      case "explore": return <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" {...p} /><path d="M15.5 8.5l-2 5-5 2 2-5z" {...p} /></svg>;
      case "roles": return <svg viewBox="0 0 24 24"><rect x="4" y="4" width="6.5" height="6.5" rx="1.5" {...p} /><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5" {...p} /><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5" {...p} /><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5" {...p} /></svg>;
      case "compare": return <svg viewBox="0 0 24 24"><path d="M12 4v16" {...p} /><path d="M5 9l-2.5 4h5z" {...p} /><path d="M19 9l-2.5 4h5z" {...p} /><path d="M5 6h4M15 6h4" {...p} /></svg>;
      case "resume": return <svg viewBox="0 0 24 24"><path d="M7 3h7l4 4v14H7z" {...p} /><path d="M13.5 3v4.5H18" {...p} /><path d="M9.5 13h5M9.5 16.5h5" {...p} /></svg>;
      case "countries": return <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" {...p} /><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18" {...p} /></svg>;
      default: return null;
    }
  };

  // ---- role action sheet (mobile openRoleMenu target) ----
  function RoleSheet({ roleId, app, onClose }) {
    const open = !!roleId;
    const role = roleId ? S().roleById(roleId) : null;
    const isFav = role && app.favs.has("role:" + role.id);
    const cd = role && role.countries[app.country];
    return (
      <>
        <div className={"m-sheet-scrim" + (open ? " show" : "")} onClick={onClose}></div>
        <div className={"m-sheet" + (open ? " show" : "")}>
          <div className="m-sheet-grip"></div>
          {role && (
            <>
              <div className="row gap12" style={{ alignItems: "center", padding: "4px 8px 14px", borderBottom: "1px solid var(--line)", marginBottom: 6 }}>
                <UI.FamilyDot family={role.family} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 16, fontWeight: 700, color: "var(--black)" }}>{role.name}</div>
                  <div className="small" style={{ color: "var(--t3)" }}>{S().fmtCur(cd.median, app.country)} · ★ {cd.score.total.toFixed(1)}</div>
                </div>
              </div>
              <button className="m-sheet-item" onClick={() => { app.go("roles", { roleId: role.id }); onClose(); }}><span className="ic">→</span>Open role dashboard</button>
              <button className="m-sheet-item" onClick={() => { app.addTray("role", role.id); onClose(); }}><span className="ic">⊕</span>Add to Compare</button>
              <button className="m-sheet-item" onClick={() => { app.toggleFav("role", role.id, role.name); onClose(); }}><span className="ic">{isFav ? "★" : "☆"}</span>{isFav ? "Remove from saved" : "Save to shelf"}</button>
            </>
          )}
        </div>
      </>
    );
  }

  // ---- saved sheet ----
  function SavedSheet({ open, app, onClose }) {
    const list = Array.from(app.favs.values());
    return (
      <>
        <div className={"m-sheet-scrim" + (open ? " show" : "")} onClick={onClose}></div>
        <div className={"m-sheet" + (open ? " show" : "")}>
          <div className="m-sheet-grip"></div>
          <div className="row between" style={{ padding: "0 8px 12px" }}>
            <div className="card-title" style={{ fontSize: 16 }}>Saved</div>
            <a className="pill ghost sm" href="index.html" style={{ textDecoration: "none" }}>Desktop ↗</a>
          </div>
          {list.length === 0 && <div className="small" style={{ color: "var(--t3)", padding: "10px 8px 16px" }}>Tap the star on any role to keep it here. No digests, no notifications.</div>}
          {list.map(f => (
            <button key={f.type + ":" + f.id} className="m-sheet-item" onClick={() => { app.go("roles", { roleId: f.id }); onClose(); }}>
              {f.family && <UI.FamilyDot family={f.family} />}{f.label}
            </button>
          ))}
        </div>
      </>
    );
  }

  // ---- compact mobile Explore ----
  function MobileExplore({ app }) {
    const code = app.country;
    const mp = S().marketPulse[code];
    const [pulse, setPulse] = useState("hottest");
    const [mode, setMode] = useState("median");
    const pulseMeta = { hottest: ["Hottest", mp.hottest, cd => cd.demand, v => v], topPay: ["Top pay", mp.topPay, cd => cd.median, v => S().fmtCompact(v, code)], rising: ["Rising", mp.rising, cd => cd.demand, v => v], topScore: ["Job Score", mp.topScore, cd => cd.score.total, v => v.toFixed(1)] };
    const [, items, getv, fmt] = pulseMeta[pulse];

    const metricMeta = { median: ["Pay", v => S().fmtCompact(v, code)], demand: ["Demand", v => v], score: ["Score", v => v.toFixed(1)] };
    const canvasItems = [...S().roles].map(r => {
      const cd = r.countries[code];
      const v = mode === "median" ? cd.median : mode === "score" ? cd.score.total : cd.demand;
      return { label: r.name, value: v, id: r.id };
    }).sort((a, b) => b.value - a.value);

    return (
      <div className="surface-enter">
        <div className="m-hero">
          <div className="eyebrow">Tech job-market intelligence · 7 markets</div>
          <div className="m-globe-wrap">
            <UI.InteractiveGlobe size={264} active={code} onSelect={app.setCountry} />
            <div className="coords" style={{ opacity: 0.6, marginTop: 2 }}>DRAG · TAP A COUNTRY</div>
          </div>
          <h1 className="display" style={{ color: "var(--black)" }}>The whole<br />job market.</h1>
          <p className="body" style={{ maxWidth: 320, margin: "12px auto 0" }}>Tap the globe to explore <strong style={{ color: "var(--t1)" }}>{S().C[code].name}</strong> — pay, demand, skills and rankings.</p>
          <div className="m-cta">
            <button className="pill solid" onClick={() => app.go("roles")}>Browse roles →</button>
            <button className="pill ghost" onClick={() => app.go("resume")}>Drop résumé</button>
          </div>
          <div className="m-stats">
            {[["16", "roles"], ["7", "countries"], ["9 yr", "trends"]].map(([n, l]) => <div key={l}><div className="n tnum">{n}</div><div className="l">{l}</div></div>)}
          </div>
        </div>

        {/* market pulse */}
        <div className="mt32">
          <div className="row between" style={{ marginBottom: 12 }}>
            <div className="sec-eyebrow" style={{ marginBottom: 0 }}>Market pulse</div>
            <UI.CountrySelect value={code} onChange={app.setCountry} compact />
          </div>
          <div className="seg" style={{ marginBottom: 12 }}>
            {Object.keys(pulseMeta).map(k => <button key={k} className={pulse === k ? "on" : ""} onClick={() => setPulse(k)}>{pulseMeta[k][0]}</button>)}
          </div>
          <div className="card pooled">
            {items.map((r, i) => {
              const cd = r.countries[code];
              return (
                <button key={r.id} onClick={(e) => window.openRoleMenu(r.id)} className="row between" style={{ width: "100%", background: "transparent", border: "none", cursor: "pointer", padding: "11px 0", borderBottom: i < items.length - 1 ? "1px solid var(--line)" : "none", textAlign: "left" }}>
                  <span className="row gap10" style={{ minWidth: 0, alignItems: "center" }}><span className="tnum" style={{ color: "var(--t3)", fontSize: 12, fontWeight: 700, width: 14 }}>{i + 1}</span><UI.FamilyDot family={r.family} /><span style={{ fontSize: 14, color: "var(--t1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span></span>
                  <span className="tnum" style={{ fontSize: 13.5, fontWeight: 700, color: "var(--black)", flexShrink: 0 }}>{fmt(getv(cd))}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* mini explore canvas */}
        <div className="mt32">
          <div className="sec-eyebrow">Explore mode</div>
          <div className="h3" style={{ color: "var(--black)", marginBottom: 12 }}>Rank roles by any axis</div>
          <div className="seg" style={{ marginBottom: 16 }}>
            {Object.keys(metricMeta).map(m => <button key={m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>{metricMeta[m][0]}</button>)}
          </div>
          <div className="card">
            <Charts.RankBars items={canvasItems} fmt={metricMeta[mode][1]} height={22} onItemClick={(it) => window.openRoleMenu(it.id)} />
          </div>
        </div>
      </div>
    );
  }

  // ---- mobile-native Roles surface (index + Job Score board) ----
  function MobileScoreRow({ role, code, app, n }) {
    const [open, setOpen] = useState(false);
    const sc = role.countries[code].score;
    const cd = role.countries[code];
    const comps = [["Demand", sc.demand], ["Pay", sc.pay], ["Opportunity", sc.opp]];
    return (
      <div style={{ borderRadius: 14, background: open ? "rgba(255,77,0,0.06)" : "transparent", border: "1px solid " + (open ? "rgba(255,77,0,0.45)" : "transparent"), marginBottom: 2, transition: "background 0.2s" }}>
        <button onClick={() => setOpen(o => !o)} className="row gap10" style={{ width: "100%", background: "transparent", border: "none", cursor: "pointer", padding: "12px 11px", textAlign: "left", alignItems: "center" }}>
          <span className="tnum" style={{ fontSize: 13, fontWeight: 700, width: 18, color: sc.rank <= 3 ? "var(--cobalt-bright)" : "var(--t3)", flexShrink: 0 }}>{sc.rank}</span>
          <UI.FamilyDot family={role.family} />
          <div className="col" style={{ minWidth: 0, flex: 1, gap: 2 }}>
            <span style={{ fontSize: 14.5, fontWeight: 600, color: "var(--black)", lineHeight: 1.2 }}>{role.name}</span>
            <span className="small" style={{ color: "var(--t3)", fontSize: 11.5 }}>{role.family.name} · {S().fmtCompact(cd.median, code)}</span>
          </div>
          <div className="col" style={{ alignItems: "flex-end", flexShrink: 0, gap: 1 }}>
            <span className="tnum" style={{ fontSize: 18, fontWeight: 700, color: "var(--black)", lineHeight: 1 }}>{sc.total.toFixed(1)}</span>
            <span className="pctile" style={{ fontSize: 10.5 }}>top {sc.pctile}%</span>
          </div>
          <span style={{ color: "var(--t3)", fontSize: 12, transform: open ? "rotate(180deg)" : "none", transition: "transform 0.2s", flexShrink: 0 }}>▾</span>
        </button>
        {open && (
          <div style={{ padding: "0 12px 14px", animation: "fadeUp 0.22s ease" }}>
            <div className="col gap10" style={{ padding: "10px 0", borderTop: "1px solid var(--line)" }}>
              {comps.map(([k, v]) => (
                <div key={k} className="row gap10" style={{ alignItems: "center" }}>
                  <span className="small" style={{ width: 86, color: "var(--t2)" }}>{k}</span>
                  <div style={{ flex: 1, height: 6, borderRadius: 9, background: "var(--wash-2)", overflow: "hidden" }}>
                    <div style={{ width: `${v * 10}%`, height: "100%", borderRadius: 9, background: "var(--bar)" }} />
                  </div>
                  <span className="tnum" style={{ width: 28, textAlign: "right", fontSize: 12.5, fontWeight: 700, color: "var(--black)" }}>{v.toFixed(1)}</span>
                </div>
              ))}
            </div>
            <button className="pill sm solid" style={{ width: "100%", justifyContent: "center", marginTop: 4 }} onClick={() => app.go("roles", { roleId: role.id })}>Open dashboard →</button>
          </div>
        )}
      </div>
    );
  }

  function MobileRoles({ app }) {
    const [q, setQ] = useState("");
    const [fam, setFam] = useState("all");
    const code = app.country;
    const filtered = S().roles.filter(r =>
      (fam === "all" || r.family.id === fam) &&
      (q === "" || r.name.toLowerCase().includes(q.toLowerCase()) || r.skills.some(s => s.name.toLowerCase().includes(q.toLowerCase()))));
    const board = [...S().roles].sort((a, b) => b.countries[code].score.total - a.countries[code].score.total).slice(0, 8);

    return (
      <div className="surface-enter">
        <div className="sec-eyebrow">Roles</div>
        <h1 className="h1" style={{ color: "var(--black)" }}>Browse every tech role</h1>
        <p className="body" style={{ marginTop: 8 }}>Tap a role for its full dashboard — pay, trend, skills, ladder and forecast.</p>

        <div className="col gap10" style={{ marginTop: 16, marginBottom: 18 }}>
          <UI.CountrySelect value={code} onChange={app.setCountry} />
          <div style={{ position: "relative" }}>
            <span style={{ position: "absolute", left: 13, top: "50%", transform: "translateY(-50%)", color: "var(--t3)" }}>⌕</span>
            <input className="input" style={{ paddingLeft: 36 }} placeholder="Search roles or skills…" value={q} onChange={e => setQ(e.target.value)} />
          </div>
          <div className="chips">
            <button className={"pill sm" + (fam === "all" ? " active" : " ghost")} onClick={() => setFam("all")}>All</button>
            {S().FAMILIES.map(f => <button key={f.id} className={"pill sm" + (fam === f.id ? " active" : " ghost")} onClick={() => setFam(f.id)}>{f.name}</button>)}
          </div>
        </div>

        {q.trim() === "" ? (
          <div className="card" style={{ padding: "16px 8px", marginBottom: 24 }}>
            <div className="row between" style={{ padding: "0 8px 10px", alignItems: "flex-start" }}>
              <div style={{ minWidth: 0 }}>
                <div className="card-title" style={{ fontSize: 15 }}>Job Score · {S().C[code].name}</div>
                <div className="card-sub">Ranked by opportunity · tap a row to see how it's built</div>
              </div>
            </div>
            {board.map(r => <MobileScoreRow key={r.id} role={r} code={code} app={app} />)}
          </div>
        ) : (
          <div className="row between" style={{ marginBottom: 14 }}>
            <div className="small" style={{ color: "var(--t2)" }}><span style={{ color: "var(--black)", fontWeight: 700 }}>{filtered.length}</span> matching "<span style={{ color: "var(--sky)" }}>{q}</span>"</div>
            <button className="pill sm ghost" onClick={() => setQ("")}>Clear ×</button>
          </div>
        )}

        <div className="col gap10">
          {filtered.map(r => {
            const cd = r.countries[code];
            return (
              <button key={r.id} onClick={() => app.go("roles", { roleId: r.id })} className="card pooled lift" style={{ cursor: "pointer", textAlign: "left", border: "1px solid var(--glass-line)", display: "block", width: "100%" }}>
                <div className="row between" style={{ marginBottom: 8 }}>
                  <span className="row gap8" style={{ alignItems: "center" }}><UI.FamilyDot family={r.family} /><span className="small" style={{ color: "var(--t3)" }}>{r.family.name}</span></span>
                  <span className="score-pill" style={{ fontSize: 15 }}>{cd.score.total.toFixed(1)}<span className="pctile" style={{ marginLeft: 5, fontSize: 10 }}>top {cd.score.pctile}%</span></span>
                </div>
                <div className="h3" style={{ color: "var(--black)", fontSize: 16 }}>{r.name}</div>
                <div className="small" style={{ color: "var(--t3)", lineHeight: 1.4, marginTop: 4 }}>{r.blurb}</div>
                <div className="row between" style={{ borderTop: "1px solid var(--line)", paddingTop: 11, marginTop: 11 }}>
                  <div><div className="tnum" style={{ fontSize: 17, fontWeight: 700, color: "var(--black)" }}>{S().fmtCur(cd.median, code)}</div><div style={{ fontSize: 10, color: "var(--t3)" }}>median · {code}</div></div>
                  <span className="tag">demand {cd.demand}</span>
                </div>
              </button>
            );
          })}
        </div>
        {filtered.length === 0 && <UI.Empty icon="⌕" title="No roles match" sub="Try a different search or clear the filter." />}
        <div style={{ height: 20 }}></div>
      </div>
    );
  }

  function MobileApp() {
    const [route, setRoute] = useState({ tab: "explore" });
    const [country, setCountry] = useState(() => localStorage.getItem("strata_country") || "IN");
    const [ppp, setPpp] = useState(false);
    const [favs, setFavs] = useState(() => { const m = new Map(); try { (JSON.parse(localStorage.getItem("strata_favs") || "[]")).forEach(f => m.set(f.type + ":" + f.id, f)); } catch (e) {} return m; });
    const [tray, setTray] = useState([]);
    const [roleMenu, setRoleMenu] = useState(null);
    const [savedOpen, setSavedOpen] = useState(false);
    const histRef = useRef([]);
    const scrollRef = useRef(null);

    useEffect(() => { window.openRoleMenu = (roleId) => setRoleMenu(roleId); }, []);
    useEffect(() => { localStorage.setItem("strata_country", country); }, [country]);
    useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = 0; }, [route.tab, route.roleId, route.mode]);

    const go = (tab, params = {}) => setRoute(prev => { histRef.current.push(prev); if (histRef.current.length > 30) histRef.current.shift(); return { tab, ...params }; });
    const back = () => setRoute(() => histRef.current.pop() || { tab: "roles" });
    const toggleFav = (type, id, label) => setFavs(prev => {
      const m = new Map(prev); const key = type + ":" + id;
      if (m.has(key)) m.delete(key); else { const role = type === "role" ? S().roleById(id) : null; m.set(key, { type, id, label: label || (role && role.name), family: role && role.family }); }
      localStorage.setItem("strata_favs", JSON.stringify(Array.from(m.values()))); return m;
    });
    const addTray = (type, id) => { setTray(prev => [...new Set([...prev, id])].slice(0, 4)); go("compare", { mode: "role" }); };
    const app = { route, go, back, country, setCountry, ppp, setPpp, favs, toggleFav, tray, setTray, addTray };

    const TABS = [["explore", "Explore"], ["roles", "Roles"], ["compare", "Compare"], ["resume", "Résumé"], ["countries", "Countries"]];

    return (
      <div className="mobile-app">
        <div className="m-top">
          <UI.Wordmark onClick={() => go("explore")} />
          <div className="row gap8">
            <UI.CountrySelect value={country} onChange={setCountry} compact />
            <button className="iconbtn" style={{ width: 36, height: 36 }} onClick={() => setSavedOpen(true)}>★</button>
          </div>
        </div>

        <div className="m-scroll" ref={scrollRef}>
          {route.tab === "explore" && <MobileExplore app={app} />}
          {route.tab === "roles" && (route.roleId ? <Roles app={app} /> : <MobileRoles app={app} />)}
          {route.tab === "compare" && <Compare app={app} />}
          {route.tab === "resume" && <Resume app={app} />}
          {route.tab === "countries" && <Countries app={app} />}
        </div>

        <nav className="m-bottom">
          {TABS.map(([k, l]) => (
            <button key={k} className={"m-tab" + (route.tab === k ? " active" : "")} onClick={() => go(k)}>
              <Icon name={k} />{l}
            </button>
          ))}
        </nav>

        <RoleSheet roleId={roleMenu} app={app} onClose={() => setRoleMenu(null)} />
        <SavedSheet open={savedOpen} app={app} onClose={() => setSavedOpen(false)} />
      </div>
    );
  }

export { MobileApp };
