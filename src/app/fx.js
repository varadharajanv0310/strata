/* ============================================================
   strata — presentation effects (no data, no behavior)
   ------------------------------------------------------------
   One delegated, rAF-throttled pointer listener that feeds the
   CSS cursor-spotlight on glass surfaces: it writes --mx/--my
   custom properties on the hovered .card / .rank-row, and the
   stylesheet does the rest (see "GLASS 2.0" in app.css).
   Purely decorative — safe to delete without touching the app.
   ============================================================ */
let attached = false;

export function initFX() {
  if (attached || typeof window === "undefined" || !window.matchMedia) return;
  attached = true;

  // pointer-precision devices only; touch gets the static treatment
  if (!window.matchMedia("(pointer: fine)").matches) return;

  let raf = 0;
  let lastEvent = null;

  const apply = () => {
    raf = 0;
    const t = lastEvent.target;
    const host = t && t.closest ? t.closest(".card, .rank-row") : null;
    if (host) {
      const r = host.getBoundingClientRect();
      host.style.setProperty("--mx", lastEvent.clientX - r.left + "px");
      host.style.setProperty("--my", lastEvent.clientY - r.top + "px");
    }
  };

  document.addEventListener(
    "pointermove",
    (e) => {
      lastEvent = e;
      if (!raf) raf = requestAnimationFrame(apply);
    },
    { passive: true }
  );
}
