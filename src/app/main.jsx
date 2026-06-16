import React from "react";
import { STRATA } from "../data/mock.js";
import { UI } from "./ui.jsx";
import { Explore } from "./explore.jsx";
import { Roles } from "./roles.jsx";
import { Compare } from "./compare.jsx";
import { Resume } from "./resume.jsx";
import { Countries } from "./countries.jsx";
import { useTweaks, TweaksPanel, TweakSection, TweakSlider, TweakColor, TweakRadio } from "../tweaks-panel.jsx";
/* ============================================================
   strata — app shell, router, favourites, tweaks
   ============================================================ */
  const { useState, useEffect, useRef } = React;
  const S = () => STRATA;

  const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
    "glow": 1,
    "accent": "#4c86f5",
    "density": "regular",
    "glass": 1,
    "starfield": true
  }/*EDITMODE-END*/;

  function Clock() {
    const [t, setT] = useState("");
    useEffect(() => {
      const tick = () => {
        const d = new Date();
        setT(d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
      };
      tick(); const i = setInterval(tick, 30000); return () => clearInterval(i);
    }, []);
    return <span className="brand-meta tnum">{t} LOCAL</span>;
  }

  function FavShelf({ open, onClose, favs, app }) {
    const list = Array.from(favs.values());
    return (
      <>
        <div className={"scrim" + (open ? " show" : "")} onClick={onClose}></div>
        <div className={"fav-shelf" + (open ? " open" : "")}>
          <div className="row between" style={{ padding: "22px 24px 16px", borderBottom: "1px solid var(--line)" }}>
            <div><div className="card-title" style={{ fontSize: 16 }}>Saved</div><div className="card-sub">{list.length} item{list.length !== 1 ? "s" : ""} · roles & comparisons</div></div>
            <button className="iconbtn" onClick={onClose}>×</button>
          </div>
          <div className="col" style={{ padding: 16, gap: 8, overflowY: "auto" }}>
            {list.length === 0 && <UI.Empty icon="☆" title="Nothing saved yet" sub="Tap the star on any role to keep it on this quiet shelf. No digests, no notifications." />}
            {list.map(f => (
              <div key={f.type + ":" + f.id} className="card tight lift" style={{ cursor: "pointer" }} onClick={() => { app.go("roles", { roleId: f.id }); onClose(); }}>
                <div className="row between">
                  <div className="row gap10" style={{ alignItems: "center" }}>
                    {f.family && <UI.FamilyDot family={f.family} />}
                    <span style={{ fontSize: 14, fontWeight: 600, color: "var(--white)" }}>{f.label}</span>
                  </div>
                  <button className="iconbtn" style={{ width: 30, height: 30 }} onClick={e => { e.stopPropagation(); app.toggleFav(f.type, f.id); }}>×</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </>
    );
  }

  function RoleMenu({ menu, app, onClose }) {
    const ref = useRef(null);
    useEffect(() => {
      if (!menu) return;
      const h = e => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
      const esc = e => { if (e.key === "Escape") onClose(); };
      document.addEventListener("mousedown", h);
      document.addEventListener("keydown", esc);
      return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", esc); };
    }, [menu]);
    if (!menu) return null;
    const role = S().roleById(menu.roleId);
    if (!role) return null;
    const W = 232;
    const x = Math.min(menu.x, window.innerWidth - W - 14);
    const y = Math.min(menu.y, window.innerHeight - 190);
    const isFav = app.favs.has("role:" + role.id);
    const cd = role.countries[app.country];
    const act = (fn) => { fn(); onClose(); };
    const Item = ({ icon, label, onClick, accent }) => (
      <button onClick={onClick} className="row gap10" style={{
        width: "100%", padding: "10px 12px", borderRadius: 9, border: "none", cursor: "pointer",
        background: "transparent", color: accent ? "var(--cobalt-deep)" : "var(--t1)", fontFamily: "var(--font)",
        fontSize: 13.5, fontWeight: 600, textAlign: "left", alignItems: "center"
      }} onMouseEnter={e => e.currentTarget.style.background = "var(--wash)"}
        onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
        <span style={{ width: 16, textAlign: "center", color: accent ? "var(--sky)" : "var(--t3)" }}>{icon}</span>{label}
      </button>
    );
    return (
      <div ref={ref} style={{
        position: "fixed", left: x, top: y, zIndex: 95, width: W,
        background: "var(--ink-2)", border: "1px solid var(--glass-line)", borderRadius: 14,
        padding: 6, boxShadow: "0 24px 64px rgba(0,0,0,0.6)", animation: "fadeUp 0.14s ease"
      }}>
        <div className="row gap10" style={{ alignItems: "center", padding: "8px 12px 10px", borderBottom: "1px solid var(--line)", marginBottom: 4 }}>
          <UI.FamilyDot family={role.family} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13.5, fontWeight: 700, color: "var(--white)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{role.name}</div>
            <div className="small" style={{ color: "var(--t3)" }}>{S().fmtCur(cd.median, app.country)} · ★ {cd.score.total.toFixed(1)}</div>
          </div>
        </div>
        <Item icon="→" label="Open role dashboard" accent onClick={() => act(() => app.go("roles", { roleId: role.id }))} />
        <Item icon="⊕" label="Add to Compare" onClick={() => act(() => app.addTray("role", role.id))} />
        <Item icon={isFav ? "★" : "☆"} label={isFav ? "Remove from saved" : "Save to shelf"} onClick={() => act(() => app.toggleFav("role", role.id, role.name))} />
      </div>
    );
  }

  function App() {
    const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
    const [route, setRoute] = useState({ tab: "explore" });
    const [country, setCountry] = useState(() => localStorage.getItem("strata_country") || "IN");
    const [ppp, setPpp] = useState(false);
    const [favs, setFavs] = useState(() => {
      const m = new Map();
      try { (JSON.parse(localStorage.getItem("strata_favs") || "[]")).forEach(f => m.set(f.type + ":" + f.id, f)); } catch (e) {}
      return m;
    });
    const [tray, setTray] = useState([]);
    const [favOpen, setFavOpen] = useState(false);
    const [roleMenu, setRoleMenu] = useState(null);
    const histRef = useRef([]);
    const scrollRef = useRef(null);

    useEffect(() => { window.openRoleMenu = (roleId, x, y) => setRoleMenu({ roleId, x, y }); }, []);

    useEffect(() => { localStorage.setItem("strata_country", country); }, [country]);
    useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = 0; }, [route.tab, route.roleId, route.mode]);

    // apply tweaks → CSS variables
    useEffect(() => {
      const r = document.documentElement.style;
      r.setProperty("--glow-strength", t.glow);
      r.setProperty("--density", { compact: 0.86, regular: 1, comfy: 1.16 }[t.density] || 1);
      r.setProperty("--glass-opacity", t.glass);
      const accent = t.accent || "#4c86f5";
      r.setProperty("--cobalt", accent);
      // derive a brighter variant
      r.setProperty("--cobalt-bright", accent === "#4c86f5" ? "#86b3ff" : accent);
    }, [t.glow, t.density, t.glass, t.accent]);

    const go = (tab, params = {}) => {
      setRoute(prev => { histRef.current.push(prev); if (histRef.current.length > 30) histRef.current.shift(); return { tab, ...params }; });
    };
    const back = () => {
      setRoute(() => { const p = histRef.current.pop(); return p || { tab: "roles" }; });
    };
    const toggleFav = (type, id, label) => {
      setFavs(prev => {
        const m = new Map(prev); const key = type + ":" + id;
        if (m.has(key)) m.delete(key);
        else { const role = type === "role" ? S().roleById(id) : null; m.set(key, { type, id, label: label || (role && role.name), family: role && role.family }); }
        localStorage.setItem("strata_favs", JSON.stringify(Array.from(m.values())));
        return m;
      });
    };
    const addTray = (type, id) => { setTray(prev => [...new Set([...prev, id])].slice(0, 4)); go("compare", { mode: "role" }); };

    const app = { route, go, back, country, setCountry, ppp, setPpp, favs, toggleFav, tray, setTray, addTray };

    const TABS = [["explore", "Explore"], ["roles", "Roles"], ["compare", "Compare"], ["resume", "Résumé"], ["countries", "Countries"]];

    return (
      <div className="app">
        {/* topbar */}
        <div className="topbar">
          <div className="row gap16" style={{ alignItems: "center" }}>
            <UI.Wordmark onClick={() => go("explore")} />
            <span className="brand-meta" style={{ opacity: 0.5 }}>·</span>
            <Clock />
          </div>
          <div className="pillnav">
            {TABS.map(([k, l]) => <button key={k} className={"tab" + (route.tab === k ? " active" : "")} onClick={() => go(k)}>{l}</button>)}
          </div>
          <div className="row gap8">
            <a className="pill ghost sm" href="mobile.html" title="Mobile version" style={{ textDecoration: "none" }}>Mobile ↗</a>
            <UI.CountrySelect value={country} onChange={setCountry} compact />
            <button className="iconbtn" onClick={() => setFavOpen(true)} title="Saved">★</button>
          </div>
        </div>

        {/* scroll region + surfaces */}
        <div className="scroll-region" ref={scrollRef}>
          <div className="shell-pad">
            {route.tab === "explore" && <Explore app={app} />}
            {route.tab === "roles" && <Roles app={app} />}
            {route.tab === "compare" && <Compare app={app} />}
            {route.tab === "resume" && <Resume app={app} />}
            {route.tab === "countries" && <Countries app={app} />}
          </div>
        </div>

        <FavShelf open={favOpen} onClose={() => setFavOpen(false)} favs={favs} app={app} />
        <RoleMenu menu={roleMenu} app={app} onClose={() => setRoleMenu(null)} />

        {/* tweaks */}
        <TweaksPanel>
          <TweakSection label="Atmosphere" />
          <TweakSlider label="Glow strength" value={t.glow} min={0.2} max={1.8} step={0.1} onChange={v => setTweak("glow", v)} />
          <TweakSlider label="Glass opacity" value={t.glass} min={0.4} max={1.6} step={0.1} onChange={v => setTweak("glass", v)} />
          <TweakSection label="Identity" />
          <TweakColor label="Accent" value={t.accent} options={["#4c86f5", "#6FA2FF", "#4FD7A0", "#E8B765"]} onChange={v => setTweak("accent", v)} />
          <TweakRadio label="Density" value={t.density} options={["compact", "regular", "comfy"]} onChange={v => setTweak("density", v)} />
        </TweaksPanel>
      </div>
    );
  }

export { App as StrataApp };
