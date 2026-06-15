/* ============================================================
   strata — the pen (presentation effects, no data, no behavior)
   ------------------------------------------------------------
   A live drafting crosshair that trails the pointer with mono
   coordinates — the instrument feel of the FIELD DESK. Desktop
   pointers only; purely decorative; safe to delete.
   ============================================================ */
let attached = false;

export function initFX() {
  if (attached || typeof window === "undefined" || !window.matchMedia) return;
  attached = true;
  if (!window.matchMedia("(pointer: fine)").matches) return;

  const mk = (cls) => {
    const el = document.createElement("div");
    el.className = cls;
    el.setAttribute("aria-hidden", "true");
    document.body.appendChild(el);
    return el;
  };
  const lineX = mk("pen-x");
  const lineY = mk("pen-y");
  const tag = mk("pen-tag");

  let tx = window.innerWidth / 2, ty = window.innerHeight / 2;
  let cx = tx, cy = ty, raf = 0;

  const loop = () => {
    cx += (tx - cx) * 0.22;
    cy += (ty - cy) * 0.22;
    lineX.style.transform = `translateY(${cy}px)`;
    lineY.style.transform = `translateX(${cx}px)`;
    tag.style.transform = `translate(${cx + 14}px, ${cy + 14}px)`;
    tag.textContent =
      "x " + String(Math.round(cx)).padStart(4, "0") +
      " · y " + String(Math.round(cy)).padStart(4, "0");
    raf = Math.abs(tx - cx) + Math.abs(ty - cy) > 0.4 ? requestAnimationFrame(loop) : 0;
  };

  document.addEventListener(
    "pointermove",
    (e) => {
      tx = e.clientX; ty = e.clientY;
      if (!raf) raf = requestAnimationFrame(loop);
    },
    { passive: true }
  );
}
