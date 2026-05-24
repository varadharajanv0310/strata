# strata — tech job-market intelligence

A free, fully public web app to explore and compare the global tech job market:
what roles pay, how demand is moving, how the same role differs across countries,
and what a résumé is worth — across **7 markets** (India, US, UK, Canada, Australia,
Singapore, Germany).

It works in **two registers under one identity**: an *atmospheric* register (the
Explore landing — deep space-black, the dotted-world globe, the blue→white glow,
big confident type) and a *data* register (dense-but-breathable dashboards,
rankings, comparisons, and charts), sharing one palette, glass language, and the
cobalt `#0033FF` accent.

This is a faithful React implementation of a design handed off from
[Claude Design](https://claude.ai/design). The original HTML/CSS/JS prototype was
ported to a real Vite + React app: the CSS and the mock-data layer are reused
verbatim (so the visual output matches the prototype), and the components were
converted from a `window.*` global namespace to ES modules.

## Run

```bash
npm install
npm run dev      # desktop:  http://localhost:5173/
                 # mobile:   http://localhost:5173/mobile.html
npm run build    # production build → dist/  (builds both entries)
npm run preview  # serve the production build
```

Plus Jakarta Sans is loaded from Google Fonts. Dark mode only — there is no light theme.

## Structure

```
index.html            desktop entry  → src/main-desktop.jsx  → <StrataApp/>
mobile.html           mobile entry   → src/main-mobile.jsx   → <MobileApp/>  (in a phone frame)
src/
  data/mock.js        deterministic mock-data layer (export const STRATA) — swap a real pipeline in here
  styles/             tokens.css · app.css · mobile.css  (the design system; reused verbatim)
  tweaks-panel.jsx    floating design-tweaks shell (glow / glass / accent / density)
  app/
    charts.jsx        SVG/canvas chart system (export const Charts)
    ui.jsx            shared primitives incl. the interactive dotted globe (export const UI)
    explore.jsx       Explore surface — atmospheric landing, Market Pulse, free-axis canvas
    roles.jsx         Roles index + Job Score board + the flagship Role Dashboard
    compare.jsx       Compare — Role vs Role, Country vs Country, Then vs Now, Market Mirror
    resume.jsx        Résumé → market value, matches, recommendations, A-vs-B
    countries.jsx     Per-country dashboards + Pay Transparency Index
    main.jsx          desktop shell: pill-tab router, favourites, role quick-menu, tweaks
    mobile.jsx        mobile shell: bottom-tab nav, bottom sheets, compact surfaces
```

## Surfaces

- **Explore** — interactive dotted-world globe (drag to spin, tap a country to redraw the
  whole page), Market Pulse (hottest / highest-paid / fastest-rising / top-score), and a
  live free-axis canvas with inline-expanding trends.
- **Roles** — searchable index, the explainable **Job Score board** (score + percentile +
  D/P/O component breakdown), and the deep **Role Dashboard**: median salary, salary-over-time,
  demand trajectory with an honest projected forecast band, skills + durability bars, the pay
  ladder, demand-vs-interest, and a PPP-fair cross-country strip.
- **Compare** — pin up to 4, unrestricted (any role × country × year), with per-chart series
  selection and a Nominal/PPP toggle.
- **Résumé** — drop a résumé → whole-profile valuation per country, PPP best-market ranking,
  role matches, a skills-gap plan, and an opt-in A-vs-B head-to-head.
- **Countries** — per-market dashboards and the **Pay Transparency Index**.

Every figure carries a clickable **confidence badge → provenance** (source, sample size,
freshness, job- vs person-level). Currencies are shown natively per country; cross-country
fairness is handled via the PPP toggle, never live FX.

## Notes

- The wordmark **"strata"** is a placeholder, per the brief — easy to swap.
- Desktop-first (the dashboards want width); the mobile build is a dedicated single-column shell.
- Data is mock but realistic and lives entirely in `src/data/mock.js`, structured so a real
  pipeline can be wired in without touching the UI.
