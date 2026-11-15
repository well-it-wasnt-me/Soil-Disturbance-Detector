/* Disturbed Ground Detector, the map UI. Plain JavaScript, no build step.
   Pages: cases, one case, new search, results of a search, one lead with its
   field sheet, jobs, imagery availability, settings. Everything talks to the
   local server; every API call carries the page's token. */
"use strict";

const NS = "http://www.w3.org/2000/svg";
const TOKEN = window.DGD.token;
const SVG_TAGS = new Set(["svg", "path", "circle", "rect", "line", "polyline", "polygon", "g", "text", "title"]);

// Helpers ---------------------------------------------------------------------

function h(tag, attrs, ...kids) {
  const el = SVG_TAGS.has(tag) ? document.createElementNS(NS, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (!SVG_TAGS.has(tag) && ["value", "checked", "selected", "disabled"].includes(k)) el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  add(el, kids);
  return el;
}
/* replaceChildren, minus its habit of printing "null" for an empty slot. */
function fill(el, ...kids) {
  el.replaceChildren();
  return add(el, kids);
}
function add(el, kids) {
  for (const k of [kids].flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);

const ICONS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  box: '<rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 3"/>',
  down: '<path d="M12 4v12M7 11l5 5 5-5M5 20h14"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  checkc: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  xc: '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  spin: '<circle cx="12" cy="12" r="9" opacity=".25"/><path d="M21 12a9 9 0 0 0-9-9"/>',
  alert: '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4M12 17.5v.01"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  print: '<path d="M7 9V4h10v5M7 17H5a1 1 0 0 1-1-1v-5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v5a1 1 0 0 1-1 1h-2"/><rect x="7" y="14" width="10" height="6"/>',
  swipe: '<path d="M12 3v18"/><path d="M8 7l-4 5 4 5M16 7l4 5-4 5"/>',
  left: '<path d="M15 6l-6 6 6 6"/>', right: '<path d="M9 6l6 6-6 6"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
};
function icon(name, extra) {
  const s = document.createElementNS(NS, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", "i" + (extra ? " " + extra : ""));
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
}

async function api(path, opts = {}) {
  const r = await fetch(path, Object.assign({}, opts, {
    headers: Object.assign({ "X-DGD-Token": TOKEN, "Content-Type": "application/json" }, opts.headers || {}),
    credentials: "same-origin",
  }));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
async function file(jid, rel, as = "json") {
  const r = await fetch(`/results/${encodeURIComponent(jid)}/${rel}`, { credentials: "same-origin" });
  if (!r.ok) return null;
  return as === "json" ? r.json().catch(() => null) : r.text();
}
const resultUrl = (jid, rel) => `/results/${encodeURIComponent(jid)}/${rel}`;

function toast(text, isError) {
  const t = h("div", { class: "toast" + (isError ? " error" : ""), role: isError ? "alert" : "status" }, text);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), isError ? 9000 : 4500);
}
async function guard(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); return undefined; }
}

const KIND = { trash: "Dumping and burning", burial_wide: "Burial, wide area", graves: "Grave search" };
const CASE_KIND = { missing_person: "Missing person", dumping: "Illegal dumping", other: "Other" };
const CASE_STATUS = { active: "Active", field_visit: "Field visit planned", on_hold: "On hold", closed: "Closed" };
const REVIEW = { unreviewed: "To review", checked: "Checked", field_visit: "Field visit", needs_imagery: "Needs imagery", dismissed: "Dismissed" };
const LEAD = { persistent: "Changed and stayed", transient: "Dug and covered", grave: "Grave candidate" };
const COLORS = () => {
  const cs = getComputedStyle(document.documentElement);
  return { persistent: cs.getPropertyValue("--persistent").trim(), transient: cs.getPropertyValue("--transient").trim(),
           grave: cs.getPropertyValue("--grave").trim(), accent: cs.getPropertyValue("--accent").trim(), muted: cs.getPropertyValue("--faint").trim() };
};
const GRAVE_LAYERS = { anomaly: "Looks unlike its surroundings", change: "Changed since the earlier image", relief: "Raised or sunken in the surface model",
                       thermal: "Warmer or colder than around it", dig: "Fresh soil and weaker plants" };
const COMPONENTS = { veg_loss: "plants lost", bare_gain: "bare soil", bright_gain: "brighter", burn_gain: "burn scar" };
const BUSY = ["queued", "running", "stopping"];

function areaKm2(bb) {
  const mid = (bb[1] + bb[3]) / 2 * Math.PI / 180;
  return Math.abs(bb[2] - bb[0]) * 111.32 * Math.cos(mid) * Math.abs(bb[3] - bb[1]) * 110.57;
}
function boxSides(bb) {
  const w = Math.abs(bb[2] - bb[0]) * 111.32 * Math.cos((bb[1] + bb[3]) / 2 * Math.PI / 180), hgt = Math.abs(bb[3] - bb[1]) * 110.57;
  return [w, hgt];
}
const fmtKm2 = a => a < 1 ? `${(a * 100).toFixed(a < 0.1 ? 2 : 1)} ha` : a >= 1000 ? `${Math.round(a).toLocaleString()} km²` : `${a.toFixed(a < 10 ? 2 : 1)} km²`;
const fmtLen = km => km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(2)} km`;
const fmtNum = (v, d = 1) => (v === null || v === undefined || Number.isNaN(+v)) ? "–" : (+v).toFixed(d);
const fmtInt = v => (v === null || v === undefined) ? "–" : Math.round(+v).toLocaleString();
function fmtBytes(b) {
  if (b === null || b === undefined) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b < 10 && i ? 1 : 0)} ${u[i]}`;
}
function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const days = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / 864e5);
  const hm = d.toTimeString().slice(0, 5);
  if (days === 0) return `Today ${hm}`;
  if (days === 1) return `Yesterday ${hm}`;
  if (days < 7) return `${days} days ago`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
const fmtDay = s => s ? new Date(s + (s.length === 10 ? "T12:00:00" : "")).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";
const esc = s => String(s).replace(/[<>&"']/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" }[c]));

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied."); } catch (e) { toast("Could not copy: " + e.message, true); }
}

// Job progress: the bar --------------------------------------------------------------

function jobFraction(j) {
  if (j.status === "done") return 1;
  const p = j.progress || {};
  if (!p.total) return 0;
  const inStep = p.of ? Math.min(1, (p.done || 0) / p.of) : 0;
  return Math.max(0, Math.min(1, ((p.step || 1) - 1 + inStep) / p.total));
}
function jobLine(j) {
  if (j.status === "queued") return "Waiting. Starts when the job before it ends.";
  if (j.status === "failed") return "Failed: " + (j.error || "see the log");
  if (j.status === "stopped" || j.status === "cancelled") return j.error || j.status;
  if (j.status === "done") {
    const s = j.summary || {}, bk = s.by_kind || {};
    const parts = Object.entries(bk).map(([k, v]) => `${v} ${k}`);
    return `${s.candidates || 0} leads` + (parts.length ? `: ${parts.join(", ")}` : "");
  }
  const p = j.progress || {};
  if (!p.total) return j.status === "stopping" ? "Stopping…" : "Starting…";
  return `Step ${p.step} of ${p.total} · ${p.label}` + (p.of ? ` · ${p.done} / ${p.of}` : "");
}
function progressBar(frac, big) {
  return h("div", { class: "progress" + (big ? " big" : ""), role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100",
                    "aria-valuenow": String(Math.round(frac * 100)) }, h("span", { style: { width: `${Math.round(frac * 100)}%` } }));
}
function statusIcon(j) {
  const map = { running: ["spin", "var(--accent)"], stopping: ["spin", "var(--danger)"], queued: ["clock", "var(--faint)"],
                done: ["checkc", "var(--ok)"], failed: ["xc", "var(--danger)"], stopped: ["xc", "var(--faint)"], cancelled: ["xc", "var(--faint)"] };
  const [name, color] = map[j.status] || ["clock", "var(--faint)"];
  const s = icon(name); s.style.width = s.style.height = "20px"; s.style.color = color;
  return s;
}
function jobRow(j, opts = {}) {
  const acts = h("div", { class: "acts" });
  if (j.status === "queued" || j.status === "running")
    acts.append(h("button", { class: "btn small danger", type: "button", onclick: () => jobAction(j.id, "stop") }, icon("stop"), j.status === "queued" ? "Cancel" : "Stop"));
  if (j.status === "done") acts.append(h("a", { class: "btn small primary", href: `#/job/${j.id}` }, "Review leads"));
  if (opts.log !== false) acts.append(h("a", { class: "btn small", href: `#/jobs/${j.id}` }, "Log"));
  return h("div", { class: "jobrow" }, statusIcon(j),
    h("div", { class: "info" },
      h("div", { class: "row", style: { gap: "8px", flexWrap: "nowrap" } }, h("span", { class: "name", title: j.name }, j.name), h("span", { class: "small muted" }, KIND[j.kind])),
      BUSY.includes(j.status) ? progressBar(jobFraction(j)) : null,
      h("div", { class: "prog-line" + (j.status === "failed" ? " danger" : "") }, jobLine(j))),
    acts);
}
async function jobAction(id, action) {
  await guard(() => post(`/api/jobs/${id}/${action}`));
  pollJobs();
}

// App state, polling, routing ----------------------------------------------------------

const S = { info: null, settings: {}, jobs: [], cases: null, leave: [], jobsListeners: new Set() };
const onLeave = fn => S.leave.push(fn);
function onJobs(fn) { S.jobsListeners.add(fn); onLeave(() => S.jobsListeners.delete(fn)); }
let pollTimer = null;
async function pollJobs() {
  clearTimeout(pollTimer);
  try {
    S.jobs = await api("/api/jobs");
    $("#side-status").classList.remove("lost");
    S.jobsListeners.forEach(fn => fn(S.jobs));
    const run = S.jobs.filter(j => j.status === "running" || j.status === "stopping").length, wait = S.jobs.filter(j => j.status === "queued").length;
    $("#side-jobs").textContent = run || wait ? `${run} running, ${wait} waiting` : "No jobs running";
    const badge = $("#nav-busy"); badge.hidden = !(run + wait); badge.textContent = run + wait;
  } catch (e) {
    $("#side-status").classList.add("lost");
    $("#side-jobs").textContent = "Lost contact with the local server";
  }
  pollTimer = setTimeout(pollJobs, S.jobs.some(j => BUSY.includes(j.status)) ? 1500 : 8000);
}

const ROUTES = [
  [/^#\/cases$/, renderCases, "cases"],
  [/^#\/case\/([\w-]+)$/, renderCase, "cases"],
  [/^#\/new(?:\?(.*))?$/, renderNew, "new"],
  [/^#\/job\/([\w-]+)$/, renderJob, "jobs"],
  [/^#\/job\/([\w-]+)\/lead\/([\w-]+)$/, renderLead, "jobs"],
  [/^#\/jobs(?:\/([\w-]+))?$/, renderJobs, "jobs"],
  [/^#\/imagery(?:\?(.*))?$/, renderImagery, "imagery"],
  [/^#\/settings$/, renderSettings, "settings"],
];
let routeSeq = 0;
async function route() {
  S.leave.splice(0).forEach(fn => { try { fn(); } catch (e) { /* already gone */ } });
  const hash = location.hash || "#/cases";
  const hit = ROUTES.map(([re, fn, nav]) => [hash.match(re), fn, nav]).find(([m]) => m);
  if (!hit) { location.hash = "#/cases"; return; }
  const [m, fn, nav] = hit;
  document.querySelectorAll("#side [data-nav]").forEach(a => { if (a.dataset.nav !== nav) a.removeAttribute("aria-current"); else a.setAttribute("aria-current", "page"); });
  const main = $("#main"), seq = ++routeSeq;
  fill(main, h("div", { class: "page" }, h("p", { class: "muted" }, "Loading…")));
  try {
    const page = await fn(...m.slice(1).map(v => v && decodeURIComponent(v)));
    if (seq !== routeSeq) return;
    fill(main, page);
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
    S.leave.push(...(page._after || []).map(f => f()).filter(Boolean));
  } catch (e) {
    if (seq !== routeSeq) return;
    fill(main, h("div", { class: "page" }, h("h1", {}, "Something went wrong"), h("p", { class: "banner danger" }, e.message)));
  }
}
// Pages that need their DOM attached before they can finish (maps) push a function here.
const after = (page, fn) => { (page._after = page._after || []).push(fn); return page; };
const params = q => Object.fromEntries(new URLSearchParams(q || ""));
const qs = o => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== "")).toString();

// Maps -------------------------------------------------------------------------------

function baseMap(el, opts = {}) {
  const map = L.map(el, { worldCopyJump: true, zoomControl: opts.zoom !== false, attributionControl: true }).setView(opts.center || [30, 10], opts.zoomLevel || 3);
  if (!S.settings.offline_map) {
    const sat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" });
    const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" });
    (opts.streets ? streets : sat).addTo(map);
    if (opts.layers !== false) map._dgdLayers = L.control.layers({ "Satellite": sat, "Streets": streets }, {}, { position: "topright" }).addTo(map);
  } else {
    map.attributionControl.addAttribution("Offline map: no tiles");
  }
  if (opts.scale !== false) L.control.scale({ imperial: false }).addTo(map);
  (opts.cleanup || S.leave).push(() => map.remove());
  return map;
}
const bboxBounds = bb => L.latLngBounds([bb[1], bb[0]], [bb[3], bb[2]]);

/* Drag a box on the map. Calls onBox(bbox) when a box is drawn. */
function boxDrawer(map, onBox) {
  let drawing = false, start = null, ghost = null;
  const hint = h("div", { class: "drawhint", hidden: true }, "Drag a box. Esc to cancel.");
  map.getContainer().parentElement.append(hint);
  const set = on => { drawing = on; map.getContainer().classList.toggle("drawing", on); hint.hidden = !on; on ? map.dragging.disable() : map.dragging.enable(); };
  const esc = e => { if (e.key === "Escape" && drawing) { set(false); if (ghost) { map.removeLayer(ghost); ghost = null; } } };
  document.addEventListener("keydown", esc); onLeave(() => document.removeEventListener("keydown", esc));
  map.on("mousedown", e => { if (!drawing) return; start = e.latlng; ghost = L.rectangle([start, start], { color: COLORS().accent, weight: 2, dashArray: "6 4", fill: false }).addTo(map); });
  map.on("mousemove", e => { if (drawing && start && ghost) ghost.setBounds([start, e.latlng]); });
  map.on("mouseup", e => {
    if (!drawing || !start) return;
    const b = L.latLngBounds(start, e.latlng); start = null; set(false);
    if (ghost) { map.removeLayer(ghost); ghost = null; }
    if (b.getNorth() === b.getSouth() || b.getEast() === b.getWest()) return;
    const w = L.Util.wrapNum(b.getWest(), [-180, 180], true), east = w + (b.getEast() - b.getWest());
    onBox([w, b.getSouth(), Math.min(east, 180), b.getNorth()].map(v => +v.toFixed(6)));
  });
  return { start: () => set(true) };
}

/* Free high resolution coverage: national programs drawn whole, OpenAerialMap per image when zoomed in. */
function coverageLayer(map) {
  const COV = { nir: "#40c057", rgb: "#9775fa", oam: "#e0a800" };
  const OAM_MIN_ZOOM = 10, OAM_TILE = 1;
  if (!map.getPane("coverage")) map.createPane("coverage").style.zIndex = 350;
  const group = L.layerGroup(), oam = L.layerGroup().addTo(group);
  const tiles = {}, seen = new Set(), centers = [];
  let national = null, asked = false, busy = 0;
  const fill = () => { const z = map.getZoom(); return z <= 7 ? 0.28 : z <= 11 ? 0.14 : 0.05; };
  const tip = (title, lines) => { const b = h("div", {}, h("b", {}, title)); lines.filter(Boolean).forEach(t => b.append(h("div", {}, t))); return b; };
  const legend = L.control({ position: "bottomright" });
  let note = null;
  legend.onAdd = () => {
    note = h("div", { class: "muted" });
    const d = h("div", { class: "legend" }, h("b", {}, "Free high resolution imagery"),
      [[COV.nir, "Whole area flown, colour and infrared"], [COV.rgb, "Whole area flown, colour only"], [COV.oam, "OpenAerialMap, single images"]]
        .map(([c, t]) => h("div", {}, h("span", { class: "swatch", style: { borderColor: c, background: c + "55" } }), t)),
      note, h("div", { class: "muted" }, "Hover a zone for pixel size and years. Edges are good to about a kilometre."));
    L.DomEvent.disableClickPropagation(d);
    return d;
  };
  const legendNote = () => {
    if (!note) return;
    const b = map.getBounds(), inView = centers.filter(c => b.contains(c)).length;
    note.textContent = map.getZoom() < OAM_MIN_ZOOM ? "Zoom in to see OpenAerialMap images." : busy ? "Asking OpenAerialMap…" : `${inView} OpenAerialMap image(s) in view.`;
  };
  async function loadNational() {
    if (asked) return; asked = true;
    try {
      const fc = await api("/api/coverage");
      national = L.geoJSON(fc, {
        pane: "coverage", style: f => ({ color: f.properties.has_nir ? COV.nir : COV.rgb, weight: 1, fillOpacity: fill() }),
        onEachFeature: (f, lyr) => { const p = f.properties; lyr.bindTooltip(tip(p.name, [`Pixel: ${p.pixel}`, `Years: ${p.years}`, `Infrared: ${p.nir}`, `${p.license}. Largest box ${p.max_km2} km².`]), { sticky: true }); },
      }).addTo(group);
    } catch (e) { asked = false; toast("Could not load the coverage map: " + e.message, true); }
  }
  function addOam(f) {
    const p = f.properties; if (seen.has(p.id)) return; seen.add(p.id);
    const t = () => tip(p.title || "Untitled image", [`${p.date || "date unknown"}, ${p.platform || ""}`, p.res_m ? `Pixel: ${Math.round(p.res_m * 100)} cm` : "", p.provider ? `By ${p.provider}` : "", `Licence: ${p.license}`]);
    const shape = L.geoJSON(f, { pane: "coverage", style: { color: COV.oam, weight: 2, dashArray: "4 3", fillOpacity: 0.1 } }).bindTooltip(t(), { sticky: true }).addTo(oam);
    const c = shape.getBounds().getCenter(); centers.push(c);
    L.circleMarker(c, { pane: "coverage", radius: 4, color: COV.oam, weight: 2, fillOpacity: 0.7 }).bindTooltip(t()).addTo(oam);
  }
  async function loadTile(x, y) {
    const k = x + "," + y; if (tiles[k]) return; tiles[k] = true; busy++; legendNote();
    try { const r = await api(`/api/coverage/oam?bbox=${x},${y},${x + OAM_TILE},${y + OAM_TILE}`); r.features.forEach(addOam); if (r.error) delete tiles[k]; }
    catch (e) { delete tiles[k]; } finally { busy--; legendNote(); }
  }
  function update() {
    if (!map.hasLayer(group)) return; legendNote();
    if (map.getZoom() < OAM_MIN_ZOOM) return;
    const b = map.getBounds();
    for (let x = Math.max(-180, Math.floor(b.getWest())); x < Math.min(180, b.getEast()); x += OAM_TILE)
      for (let y = Math.max(-85, Math.floor(b.getSouth())); y < Math.min(85, b.getNorth()); y += OAM_TILE) loadTile(x, y);
  }
  group.on("add", () => { legend.addTo(map); loadNational(); update(); });
  group.on("remove", () => legend.remove());
  map.on("zoomend", () => { if (national) national.setStyle({ fillOpacity: fill() }); });
  map.on("moveend", update);
  if (map._dgdLayers) map._dgdLayers.addOverlay(group, "Free high resolution imagery");
  return group;
}

function waybackControl(map) {
  if (!S.settings.wayback || S.settings.offline_map) return;
  const ctl = L.control({ position: "topright" });
  let layer = null;
  const sel = h("select", { "aria-label": "Older imagery", onchange: () => {
    if (layer) { map.removeLayer(layer); layer = null; }
    if (!sel.value) return;
    layer = L.tileLayer(`https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/${sel.value}/{z}/{y}/{x}`,
      { maxZoom: 19, attribution: "Esri World Imagery Wayback (viewing only)" }).addTo(map);
  } }, h("option", { value: "" }, "Today"));
  ctl.onAdd = () => { const d = h("div", { class: "mapctl" }, icon("history"), h("label", {}, "Imagery from"), sel); L.DomEvent.disableClickPropagation(d); return d; };
  ctl.addTo(map);
  api("/api/wayback").then(rel => rel.forEach(r => sel.append(h("option", { value: r.release }, r.date)))).catch(() => ctl.remove());
}

// Cases ------------------------------------------------------------------------------

function caseStatusChip(st) { return h("span", { class: "chip " + st }, CASE_STATUS[st] || st); }
function caseSub(c) {
  return [CASE_KIND[c.kind] || c.kind, c.kind === "missing_person" && c.missing_since ? `missing since ${fmtDay(c.missing_since)}` : null,
          c.description ? c.description.slice(0, 80) : null].filter(Boolean).join(" · ");
}

function caseDialog(existing, onDone) {
  const isNew = !existing;
  const name = h("input", { class: "input", id: "cd-name", required: true, value: existing ? existing.name : "", placeholder: "e.g. Case 114, or ENV-2026-031 Canal corridor" });
  const kind = h("select", { class: "input", id: "cd-kind" }, Object.entries(CASE_KIND).map(([k, v]) => h("option", { value: k, selected: existing ? existing.kind === k : k === "other" }, v)));
  const since = h("input", { class: "input", id: "cd-since", type: "date", value: existing && existing.missing_since || "" });
  const desc = h("textarea", { class: "input", id: "cd-desc", rows: "3", placeholder: "What the case is about. Shown on the case page." }, existing ? existing.description : "");
  const sinceField = h("div", { class: "field" }, h("label", { for: "cd-since" }, "Missing since"), since);
  const sync = () => { sinceField.hidden = kind.value !== "missing_person"; };
  kind.addEventListener("change", sync); sync();
  const dlg = h("dialog", { "aria-labelledby": "cd-title" });
  const form = h("form", { method: "dialog", onsubmit: async e => {
    e.preventDefault();
    const body = { name: name.value, kind: kind.value, missing_since: kind.value === "missing_person" ? since.value : null, description: desc.value };
    const r = await guard(() => isNew ? post("/api/cases", body) : post(`/api/cases/${existing.id}`, body));
    if (r) { dlg.close(); dlg.remove(); onDone(r); }
  } },
    h("h2", { id: "cd-title" }, isNew ? "New case" : "Edit case"),
    h("div", { class: "field" }, h("label", { for: "cd-name" }, "Name"), name),
    h("div", { class: "field" }, h("label", { for: "cd-kind" }, "Kind"), kind),
    sinceField,
    h("div", { class: "field" }, h("label", { for: "cd-desc" }, "Description"), desc),
    h("div", { class: "row", style: { justifyContent: "flex-end" } },
      h("button", { class: "btn", type: "button", onclick: () => { dlg.close(); dlg.remove(); } }, "Cancel"),
      h("button", { class: "btn primary", type: "submit" }, isNew ? "Create case" : "Save")));
  dlg.append(form); document.body.append(dlg); dlg.showModal(); name.focus();
}

async function renderCases() {
  const data = await api("/api/cases");
  S.cases = data.cases;
  let tab = "open", filter = "";
  const open = data.cases.filter(c => c.status !== "closed");
  const tbody = h("tbody");
  const tabs = h("div", { class: "tabs", role: "tablist", style: { padding: "0 10px" } });
  const draw = () => {
    fill(tabs, ...[["open", `Open ${open.length}`], ["closed", `Closed ${data.cases.length - open.length}`], ["all", "All"]].map(([k, t]) =>
      h("button", { role: "tab", "aria-selected": String(tab === k), onclick: () => { tab = k; draw(); } }, t)));
    const rows = data.cases.filter(c => (tab === "all" || (tab === "open") === (c.status !== "closed")) &&
      (!filter || (c.name + " " + c.description).toLowerCase().includes(filter)));
    fill(tbody, ...rows.map(c => h("tr", { class: "click", onclick: () => { location.hash = `#/case/${c.id}`; } },
      h("td", {}, h("a", { href: `#/case/${c.id}`, style: { fontWeight: 600, color: "var(--ink)" } }, c.name), h("div", { class: "small muted" }, caseSub(c))),
      h("td", {}, c.areas), h("td", {}, c.searches, c.running ? h("div", { class: "small muted" }, `${c.running} running`) : null),
      h("td", {}, c.to_review ? h("b", {}, c.to_review) : h("span", { class: "muted" }, "none"),
        c.graves_to_review ? h("div", { class: "small muted" }, `${c.graves_to_review} grave candidates`) : null),
      h("td", {}, fmtWhen(c.last_activity)), h("td", {}, caseStatusChip(c.status)))));
    if (!rows.length) tbody.append(h("tr", {}, h("td", { colspan: "6", class: "empty" }, data.cases.length ? "No cases match." : "No cases yet. Searches can still run without one.")));
  };
  draw();
  const busy = S.jobs.filter(j => BUSY.includes(j.status)).length;
  const page = h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", { class: "grow" }, h("h1", {}, "Cases"), h("p", { class: "sub" }, "Every area, search, lead and note belongs to a case, so a case can be handed over or exported whole.")),
      h("div", { style: { position: "relative", flex: "0 1 300px" } }, h("label", { for: "find", class: "sr" }, "Find a case"),
        h("input", { id: "find", class: "input", type: "search", placeholder: "Find a case", oninput: e => { filter = e.target.value.toLowerCase(); draw(); } })),
      h("button", { class: "btn primary", type: "button", onclick: () => caseDialog(null, c => { location.hash = `#/case/${c.id}`; }) }, icon("plus"), "New case")),
    h("div", { class: "tiles" },
      h("div", { class: "tile" }, h("div", { class: "k" }, "Open cases"), h("div", { class: "v" }, open.length)),
      h("div", { class: "tile" }, h("div", { class: "k" }, "Leads waiting for review"), h("div", { class: "v" }, open.reduce((a, c) => a + c.to_review, 0))),
      h("div", { class: "tile" }, h("div", { class: "k" }, "Field visits planned"), h("div", { class: "v" }, open.reduce((a, c) => a + c.field_visits, 0))),
      h("div", { class: "tile" }, h("div", { class: "k" }, "Jobs running or waiting"), h("div", { class: "v" }, busy))),
    h("div", { class: "split" },
      h("section", { class: "main card", "aria-label": "Case list", style: { overflow: "hidden", gap: 0 } }, tabs,
        h("div", { class: "tblwrap" }, h("table", { class: "tbl" },
          h("thead", {}, h("tr", {}, ["Case", "Areas", "Searches", "To review", "Last activity", "Status"].map(t => h("th", {}, t)))), tbody)),
        data.unassigned ? h("div", { style: { padding: "10px 16px", borderTop: "1px solid var(--line-2)" }, class: "small" },
          `${data.unassigned} searches are not in a case. `, h("a", { href: "#/jobs" }, "See them in Jobs")) : null),
      h("section", { class: "aside card pad", "aria-labelledby": "act-h" }, h("h2", { id: "act-h" }, "Recent activity"),
        data.activity.length ? h("ol", { class: "list-plain small" }, data.activity.map(e => h("li", {},
          h("div", {}, h("a", { href: `#/case/${e.case}`, style: { fontWeight: 600 } }, e.case_name), ": ", e.text),
          h("div", { class: "muted" }, fmtWhen(e.time))))) : h("p", { class: "muted small" }, "Nothing yet."))));
  return page;
}

async function renderCase(cid) {
  let d = await api(`/api/cases/${cid}`);
  let tab = "overview";
  const body = h("div");
  const tabs = h("div", { class: "tabs", role: "tablist" });
  const reload = async () => { d = await api(`/api/cases/${cid}`); draw(); };
  const statusSel = h("select", { class: "input", "aria-label": "Case status", style: { width: "auto" }, onchange: async e => {
    await guard(() => post(`/api/cases/${cid}`, { status: e.target.value })); reload();
  } }, Object.entries(CASE_STATUS).map(([k, v]) => h("option", { value: k, selected: d.status === k }, v)));
  const head = h("div");
  const drawHead = () => fill(head, 
    h("div", { class: "crumbs" }, h("a", { href: "#/cases" }, "Cases"), " / ", d.name),
    h("div", { class: "page-head", style: { marginTop: "8px" } },
      h("div", { class: "grow" }, h("div", { class: "row" }, h("h1", {}, d.name), caseStatusChip(d.status)),
        h("p", { class: "sub" }, [CASE_KIND[d.kind], d.missing_since ? `missing since ${fmtDay(d.missing_since)}` : null, `opened ${fmtDay(d.created.slice(0, 10))}`].filter(Boolean).join(" · ")),
        d.description ? h("p", { class: "sub" }, d.description) : null),
      statusSel,
      h("button", { class: "btn", type: "button", onclick: () => caseDialog(d, reload) }, "Edit"),
      h("a", { class: "btn", href: `/export/case/${cid}.zip`, download: "" }, icon("down"), "Export case file"),
      h("a", { class: "btn primary", href: `#/new?${qs({ case: cid })}` }, icon("plus"), "New search in this case")));
  const toReview = () => d.lead_list.filter(l => l.status === "unreviewed");
  const leadLink = l => `#/job/${l.job}/lead/${l.id}`;
  const draw = () => {
    drawHead();
    const T = [["overview", "Overview"], ["searches", `Searches ${d.jobs.length}`], ["leads", `Leads ${d.lead_list.length}`], ["log", "Log"], ["files", "Files and hashes"]];
    fill(tabs, ...T.map(([k, t]) => h("button", { role: "tab", "aria-selected": String(tab === k), onclick: () => { tab = k; draw(); } }, t)));
    mapLeave.splice(0).forEach(fn => { try { fn(); } catch (e) { /* map gone */ } });
    fill(body, ({ overview, searches, leads, log, files })[tab]());
    if (tab === "overview") requestAnimationFrame(overviewMap);
  };
  let mapEl = null;
  const mapLeave = [];
  onLeave(() => mapLeave.splice(0).forEach(fn => { try { fn(); } catch (e) { /* gone */ } }));
  function overviewMap() {
    if (!mapEl || !mapEl.isConnected) return;
    const map = baseMap(mapEl, { zoomLevel: 3, cleanup: mapLeave });
    const C = COLORS(); const all = [];
    d.jobs.forEach(j => { const r = L.rectangle(bboxBounds(j.bbox), { color: C.accent, weight: 2, fillOpacity: 0.08 }).bindTooltip(esc(`${j.name}: ${KIND[j.kind]}`)).addTo(map); all.push(r.getBounds()); });
    d.lead_list.forEach(l => {
      if (l.lat === null || l.lon === null) return;
      const closed = l.status === "dismissed" || l.status === "checked";
      L.circleMarker([l.lat, l.lon], { radius: 6, color: "#fff", weight: 1.5, fillOpacity: 1, fillColor: closed ? C.muted : (C[l.kind] || C.grave) })
        .bindTooltip(esc(`${l.id} · ${LEAD[l.kind] || l.kind} · ${REVIEW[l.status]}`)).on("click", () => { location.hash = leadLink(l); }).addTo(map);
    });
    if (all.length) map.fitBounds(all.reduce((a, b) => a.extend(b), L.latLngBounds(all[0].getSouthWest(), all[0].getNorthEast())).pad(0.15));
    setTimeout(() => map.invalidateSize(), 50);
  }
  function overview() {
    mapEl = h("div", { style: { height: "330px" } });
    const areas = d.jobs.map(j => h("tr", { class: "click", onclick: () => { location.hash = j.status === "done" ? `#/job/${j.id}` : `#/jobs/${j.id}`; } },
      h("td", {}, h("b", {}, j.name)), h("td", {}, fmtKm2(areaKm2(j.bbox))), h("td", {}, KIND[j.kind]),
      h("td", {}, j.status === "done" ? `${j.leads} leads, ${j.to_review} to review` : h("span", { class: "chip " + j.status }, j.status))));
    return h("div", { class: "split" },
      h("div", { class: "main" },
        h("section", { class: "card", "aria-label": "Map of the case", style: { overflow: "hidden" } }, mapEl,
          h("div", { class: "row small muted", style: { padding: "10px 14px", borderTop: "1px solid var(--line-2)", gap: "16px" } },
            h("span", { class: "row", style: { gap: "6px" } }, h("span", { class: "sw grave" }), "Grave candidate"),
            h("span", { class: "row", style: { gap: "6px" } }, h("span", { class: "sw persistent" }), "Changed and stayed"),
            h("span", { class: "row", style: { gap: "6px" } }, h("span", { class: "sw transient" }), "Dug and covered"),
            h("span", {}, "Grey: checked or dismissed"))),
        h("section", { class: "card", "aria-labelledby": "ar-h", style: { overflow: "hidden" } }, h("h2", { id: "ar-h", style: { padding: "14px 16px 4px" } }, "Searches in this case"),
          d.jobs.length ? h("div", { class: "tblwrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Name", "Size", "Kind", "Leads"].map(t => h("th", {}, t)))), h("tbody", {}, areas)))
            : h("div", { class: "empty" }, "No searches yet. ", h("a", { href: `#/new?${qs({ case: cid })}` }, "Start one")))),
      h("div", { class: "aside" },
        h("section", { class: "card pad", "aria-labelledby": "rv-h" }, h("div", { class: "card-head" }, h("h2", { id: "rv-h" }, "Waiting for review"), h("span", { class: "small muted" }, `${toReview().length} of ${d.lead_list.length}`)),
          toReview().length ? toReview().slice(0, 5).map(l => h("a", { href: leadLink(l), class: "leadrow", style: { textDecoration: "none", color: "inherit" } },
            h("span", { class: "sw " + l.kind }), h("span", {}, h("span", { class: "mono" }, l.id), " ", h("span", { class: "small muted" }, l.job_name)),
            h("span", { class: "score" }, fmtNum(l.score)),
            h("span", { class: "meta" }, l.kind === "grave" ? `${fmtNum(l.length_m)} × ${fmtNum(l.width_m)} m` : `${fmtInt(l.area_m2)} m² · ${l.landcover || ""}`)))
            : h("p", { class: "muted small" }, "Nothing waiting."),
          toReview().length > 5 ? h("button", { class: "btn small", type: "button", onclick: () => { tab = "leads"; draw(); } }, `All ${toReview().length}`) : null),
        h("section", { class: "card pad", "aria-labelledby": "lg-h" }, h("h2", { id: "lg-h" }, "Case log"),
          h("ol", { class: "list-plain small" }, d.events.slice(0, 6).map(e => h("li", {}, h("div", { class: "muted" }, fmtWhen(e.time)), e.text))),
          h("button", { class: "btn small", type: "button", onclick: () => { tab = "log"; draw(); } }, "Full log and notes")),
        h("section", { class: "card pad", "aria-labelledby": "fh-h" }, h("h2", { id: "fh-h" }, "Files and hashes"),
          h("p", { class: "small muted", style: { margin: 0 } }, "Each search keeps its inputs, settings, software version and a SHA-256 of every output in manifest.json."),
          h("button", { class: "btn small", type: "button", onclick: () => { tab = "files"; draw(); } }, "Check the files"))));
  }
  function searches() {
    return h("section", { class: "card", style: { overflow: "hidden" } },
      d.jobs.length ? d.jobs.slice().reverse().map(j => jobRow(j)) : h("div", { class: "empty" }, "No searches yet."),
      h("div", { style: { padding: "12px 16px", borderTop: "1px solid var(--line-2)" } }, h("a", { class: "btn primary small", href: `#/new?${qs({ case: cid })}` }, icon("plus"), "New search in this case")));
  }
  function leads() {
    let f = "unreviewed";
    const tb = h("tbody"), pills = h("div", { class: "pills", style: { padding: "12px 16px" } });
    const drawL = () => {
      const counts = Object.fromEntries(Object.keys(REVIEW).map(k => [k, d.lead_list.filter(l => l.status === k).length]));
      fill(pills, h("button", { "aria-pressed": String(f === "all"), onclick: () => { f = "all"; drawL(); } }, `All ${d.lead_list.length}`),
        ...Object.entries(REVIEW).filter(([k]) => counts[k]).map(([k, v]) => h("button", { "aria-pressed": String(f === k), onclick: () => { f = k; drawL(); } }, `${v} ${counts[k]}`)));
      const rows = d.lead_list.filter(l => f === "all" || l.status === f);
      fill(tb, ...rows.map(l => h("tr", { class: "click", onclick: () => { location.hash = leadLink(l); } },
        h("td", {}, h("span", { class: "row", style: { gap: "8px", flexWrap: "nowrap" } }, h("span", { class: "sw " + l.kind }), h("a", { href: leadLink(l), class: "mono" }, l.id))),
        h("td", {}, l.job_name), h("td", {}, LEAD[l.kind] || l.kind), h("td", { class: "mono" }, fmtNum(l.score)),
        h("td", {}, l.kind === "grave" ? `${fmtNum(l.length_m)} × ${fmtNum(l.width_m)} m` : `${fmtInt(l.area_m2)} m²`),
        h("td", {}, h("span", { class: "chip " + l.status }, REVIEW[l.status]), l.reason ? h("div", { class: "small muted" }, l.reason) : null))));
      if (!rows.length) tb.append(h("tr", {}, h("td", { colspan: "6", class: "empty" }, "No leads here.")));
    };
    drawL();
    return h("section", { class: "card", style: { overflow: "hidden" } }, pills, h("div", { class: "tblwrap" },
      h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Lead", "Search", "Kind", "Score", "Size", "Status"].map(t => h("th", {}, t)))), tb)));
  }
  function log() {
    const ta = h("textarea", { class: "input", id: "note", rows: "3", placeholder: "Add a note to the case log: who looked, what they saw, what happens next." });
    return h("div", { class: "split" },
      h("section", { class: "main card pad" }, h("h2", {}, "Case log"),
        h("ol", { class: "list-plain" }, d.events.map(e => h("li", { style: { display: "grid", gridTemplateColumns: "150px 1fr", gap: "10px" } },
          h("span", { class: "small muted" }, new Date(e.time).toLocaleString()),
          h("span", {}, e.lead && e.job ? h("a", { href: `#/job/${e.job}/lead/${e.lead}`, class: "mono" }, e.lead) : null, e.lead ? " " : "", e.text))))),
      h("section", { class: "aside card pad" }, h("label", { for: "note", class: "label" }, "Add a note"), ta,
        h("button", { class: "btn primary", type: "button", onclick: async () => {
          const r = await guard(() => post(`/api/cases/${cid}/note`, { text: ta.value })); if (r) { ta.value = ""; reload(); }
        } }, "Add to the log")));
  }
  function files() {
    const done = d.jobs.filter(j => j.status === "done");
    const rows = done.map(j => {
      const res = h("td", { class: "small" }, h("span", { class: "muted" }, "not checked yet"));
      const check = async () => {
        fill(res, "Checking…");
        const r = await guard(() => post(`/api/jobs/${j.id}/verify`));
        if (!r) { fill(res, "Could not check."); return; }
        if (r.error) { fill(res, h("span", { class: "chip warn" }, r.error)); return; }
        const bad = r.changed.length + r.missing.length;
        fill(res, h("span", { class: "chip " + (bad ? "danger" : "ok") }, bad ? `${bad} of ${r.files} files differ` : `${r.ok} files match`),
          bad ? h("div", { class: "muted" }, [...r.changed.map(f => `changed: ${f}`), ...r.missing.map(f => `missing: ${f}`)].join(", ")) : null,
          h("div", { class: "muted mono", title: "SHA-256 of manifest.json" }, `manifest ${r.manifest_sha256.slice(0, 12)}…`));
        return bad;
      };
      res._check = check;
      return [h("tr", {}, h("td", {}, h("b", {}, j.name), h("div", { class: "small muted" }, KIND[j.kind])), h("td", {}, fmtWhen(j.finished)),
        h("td", {}, h("a", { href: resultUrl(j.id, "manifest.json"), target: "_blank", rel: "noopener" }, "manifest.json")), res,
        h("td", {}, h("button", { class: "btn small", type: "button", onclick: check }, "Check"))), res];
    });
    return h("section", { class: "card", style: { overflow: "hidden" } },
      h("div", { class: "row", style: { padding: "14px 16px" } }, h("div", { class: "grow" }, h("h2", {}, "Files and hashes"),
        h("p", { class: "small muted", style: { margin: "2px 0 0" } }, "Checking recomputes the SHA-256 of every output a search wrote and compares it with its manifest.")),
        h("button", { class: "btn", type: "button", onclick: async () => { let bad = 0; for (const [, r] of rows) bad += (await r._check()) || 0; toast(bad ? `${bad} files differ from their manifests.` : "Every file matches its manifest.", !!bad); } }, "Check all"),
        h("a", { class: "btn primary", href: `/export/case/${cid}.zip`, download: "" }, icon("down"), "Export case file")),
      done.length ? h("div", { class: "tblwrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Search", "Finished", "Manifest", "Result", ""].map(t => h("th", {}, t)))),
        h("tbody", {}, rows.map(([tr]) => tr)))) : h("div", { class: "empty" }, "No finished searches yet."));
  }
  const banner = d.kind === "missing_person" ? h("div", { class: "banner danger", role: "note" }, icon("alert"),
    h("span", {}, h("b", {}, "Do not dig. "), "If a lead looks like a grave on the ground: stop, mark it, keep people and dogs off it, photograph it, and call the forensic team.")) : null;
  onJobs(jobs => { if (jobs.some(j => j.case_id === cid && BUSY.includes(j.status)) && tab === "searches") reload(); });
  draw();
  return h("div", { class: "page" }, head, banner, tabs, body);
}

// New search -------------------------------------------------------------------------

async function renderNew(q) {
  const P = params(q);
  if (!S.cases) S.cases = (await api("/api/cases")).cases;
  const today = S.info.today;
  const state = { kind: P.kind || "trash", bbox: P.bbox ? P.bbox.split(",").map(Number) : null, case: P.case || "", sources: [], years: null };
  const caseObj = () => S.cases.find(c => c.id === state.case);
  if (!P.kind && caseObj() && caseObj().kind === "missing_person") state.kind = "burial_wide";
  const panel = h("aside", { class: "panel", "aria-label": "Set up a search" });
  const mapwrap = h("div", { class: "mapwrap" }), mapEl = h("div", { class: "map" });
  mapwrap.append(mapEl);
  let map = null, rect = null, drawer = null, cov = null;

  // Inputs that survive redraws.
  const from = new Date(today); from.setMonth(from.getMonth() - 6);
  const I = {
    name: h("input", { class: "input", id: "n-name", placeholder: "e.g. Canal corridor, east of town" }),
    start: h("input", { class: "input", id: "n-start", type: "date", value: from.toISOString().slice(0, 10) }),
    end: h("input", { class: "input", id: "n-end", type: "date", value: today }),
    base: 3,
    since: h("input", { class: "input", id: "n-since", type: "date", value: (caseObj() && caseObj().missing_since) || "" }),
    until: h("input", { class: "input", id: "n-until", type: "date" }),
    src: h("select", { class: "input", id: "n-src" }),
    ay: h("select", { class: "input", id: "n-ay" }), by: h("select", { class: "input", id: "n-by" }),
    after: h("input", { class: "input mono", id: "n-after", placeholder: "/path/to/drone_ortho.tif" }),
    before: h("input", { class: "input mono", id: "n-before", placeholder: "/path/to/earlier.tif (optional)" }),
    bands: h("select", { class: "input", id: "n-bands" }, h("option", { value: "" }, "Guess from the band count"),
      ["rgb", "rgbn", "bgr", "bgrn", "nrg", "sequoia", "m3m", "p4m", "micasense"].map(b => h("option", { value: b }, b))),
    osm: h("input", { class: "input mono", id: "n-osm", placeholder: "/path/to/region-latest.osm.pbf (optional)" }),
    mass: h("input", { type: "checkbox", id: "n-mass" }),
    caseSel: h("select", { class: "input", id: "n-case" }, h("option", { value: "" }, "No case"),
      S.cases.filter(c => c.status !== "closed").map(c => h("option", { value: c.id, selected: c.id === state.case }, c.name))),
    q: h("input", { class: "input", id: "n-q", type: "search", placeholder: "Town, address or lat, lon", autocomplete: "off" }),
  };
  const qres = h("div", { class: "small" });
  I.caseSel.addEventListener("change", () => { state.case = I.caseSel.value; const c = caseObj(); if (c && c.missing_since && !I.since.value) I.since.value = c.missing_since; });
  I.src.addEventListener("change", () => { loadYears(); draw(); });
  I.ay.addEventListener("change", draw);

  function limit() {
    if (state.kind !== "graves") return S.info.limits_km2[state.kind];
    if (I.src.value === "file" || !I.src.value) return S.info.limits_km2.graves_file;
    const s = state.sources.find(x => x.key === I.src.value); return s ? s.max_km2 : S.info.limits_km2.graves_file;
  }
  async function loadSources() {
    const keep = I.src.value;
    fill(I.src, h("option", { value: "file" }, "My own image (drone or aerial GeoTIFF on this computer)"));
    state.sources = [];
    if (state.bbox) {
      state.sources = await api(`/api/sources?bbox=${state.bbox.join(",")}`).catch(() => []);
      state.sources.forEach(s => I.src.append(h("option", { value: s.key }, `${s.name}, ${Math.round(s.res_m * 100)} cm`)));
    }
    I.src.value = [...I.src.options].some(o => o.value === keep) ? keep : (state.sources[0] ? state.sources[0].key : "file");
    await loadYears();
  }
  async function loadYears() {
    state.years = null;
    if (I.src.value === "file" || !state.bbox) { draw(); return; }
    state.years = "loading"; draw();
    const r = await api(`/api/source_years?source=${I.src.value}&bbox=${state.bbox.join(",")}`).catch(e => ({ years: [], error: e.message }));
    state.years = r;
    fill(I.ay); fill(I.by, h("option", { value: "" }, "Nothing, search one image"));
    r.years.slice().reverse().forEach(a => {
      const t = `${a.year}  (${Math.round(a.res_m * 100)} cm${a.nir ? ", infrared" : ""})`;
      I.ay.append(h("option", { value: a.year, title: a.label }, t)); I.by.append(h("option", { value: a.year, title: a.label }, t));
    });
    if (r.years.length > 1) I.by.value = r.years[r.years.length - 2].year;
    draw();
  }
  function setBox(bb) {
    state.bbox = bb;
    if (rect) map.removeLayer(rect);
    rect = L.rectangle(bboxBounds(bb), { color: COLORS().accent, weight: 2.5, fillOpacity: 0.07 }).addTo(map);
    if (state.kind === "graves") loadSources(); else draw();
  }
  async function placeSearch() {
    const text = I.q.value.trim(); if (!text || !map) return;
    const m = text.match(/^\s*(-?\d+(\.\d+)?)\s*[, ]\s*(-?\d+(\.\d+)?)\s*$/);
    if (m) { map.setView([+m[1], +m[3]], 15); return; }
    qres.textContent = "Searching…";
    try {
      const r = await fetch("https://nominatim.openstreetmap.org/search?format=json&limit=5&q=" + encodeURIComponent(text));
      const res = await r.json(); fill(qres);
      if (!res.length) { qres.textContent = "Nothing found."; return; }
      res.forEach((p, i) => {
        const go = () => { const bb = p.boundingbox.map(Number); map.fitBounds([[bb[0], bb[2]], [bb[1], bb[3]]]); };
        qres.append(h("a", { href: "#", style: { display: "block" }, onclick: e => { e.preventDefault(); go(); } }, p.display_name));
        if (i === 0) go();
      });
    } catch (e) { qres.textContent = "Place search failed: " + e.message; }
  }
  I.q.addEventListener("keydown", e => { if (e.key === "Enter") placeSearch(); });

  async function run() {
    if (!state.bbox) { toast("Draw the area first: Draw box, then drag on the map.", true); return; }
    const lim = limit(), a = areaKm2(state.bbox);
    if (lim && a > lim) { toast(`The box is ${a.toFixed(2)} km². The limit here is ${lim} km². Draw a smaller one.`, true); return; }
    let params = {};
    if (state.kind === "trash") params = { start: I.start.value, end: I.end.value, baseline_years: I.base };
    if (state.kind === "burial_wide") { if (!I.since.value) { toast("Give the date the person went missing.", true); return; } params = { since: I.since.value, end: I.until.value }; }
    if (state.kind === "graves") {
      params = { source: I.src.value, osm_file: I.osm.value.trim(), mass: I.mass.checked };
      if (I.src.value === "file") Object.assign(params, { after_path: I.after.value.trim(), before_path: I.before.value.trim(), bands: I.bands.value });
      else Object.assign(params, { after_year: I.ay.value, before_year: I.by.value });
    }
    const job = await guard(() => post("/api/jobs", { kind: state.kind, name: I.name.value, bbox: state.bbox, params, case_id: state.case || null }));
    if (job) { toast("Queued. Progress shows below the map."); pollJobs(); }
  }

  function draw() {
    const lim = limit(), a = state.bbox ? areaKm2(state.bbox) : 0;
    const stepHead = (n, title, done) => h("div", { class: "step-head" }, h("span", { class: "step-num" + (done ? " done" : "") }, done ? icon("check") : n), h("h2", {}, title));
    const kindCard = (k, title, desc) => h("label", { class: "kind" + (state.kind === k ? " on" : "") },
      h("input", { type: "radio", name: "kind", checked: state.kind === k, onchange: () => { state.kind = k; if (k === "graves" && state.bbox) loadSources(); if (cov && map) { k === "graves" ? cov.addTo(map) : map.removeLayer(cov); } draw(); } }),
      h("span", {}, h("b", {}, title), h("span", { class: "d" }, desc)));
    const sides = state.bbox ? boxSides(state.bbox) : null;
    const meterCls = !state.bbox ? "" : a > lim ? " meter-over" : " meter-ok";
    const details = [];
    if (state.kind === "trash") {
      const y0 = new Date(I.start.value || today).getFullYear();
      details.push(h("div", { class: "grid2" }, h("div", { class: "field" }, h("label", { for: "n-start" }, "From"), I.start), h("div", { class: "field" }, h("label", { for: "n-end" }, "To"), I.end)),
        h("div", { class: "field" }, h("span", { class: "label", id: "base-l" }, "Normal looks like the same season in the previous"),
          h("div", { class: "seg", role: "group", "aria-labelledby": "base-l" }, [2, 3, 4, 5].map(n => h("button", { type: "button", "aria-pressed": String(I.base === n), onclick: () => { I.base = n; draw(); } }, `${n} years`))),
          h("span", { class: "small muted" }, `${Array.from({ length: I.base }, (_, i) => y0 - I.base + i).join(", ")} count as normal.`)),
        h("details", { class: "card", style: { padding: "10px 12px" } }, h("summary", { style: { cursor: "pointer", fontWeight: 500 } }, "Thresholds"),
          h("p", { class: "small muted", style: { margin: "6px 0 0" } }, `Change score ${S.settings.dumping.min_score} · smallest lead ${S.settings.dumping.min_area_m2} m² · lasts ${S.settings.dumping.min_event_days} days. `,
            h("a", { href: "#/settings" }, "Change in Settings"))));
    } else if (state.kind === "burial_wide") {
      details.push(h("div", { class: "grid2" }, h("div", { class: "field" }, h("label", { for: "n-since" }, "Missing since"), I.since), h("div", { class: "field" }, h("label", { for: "n-until" }, "Until (optional)"), I.until)),
        h("p", { class: "small muted", style: { margin: 0 } }, "Finds machine-dug pits, burning and fresh tracks. It cannot see a hand-dug grave. Use it to decide where to fly, never to clear an area."));
    } else {
      details.push(h("div", { class: "banner danger" }, icon("alert"), h("span", {}, h("b", {}, "Do not dig. "), "Leads are for a careful look on the ground, by people trained for it.")),
        h("div", { class: "field" }, h("label", { for: "n-src" }, "Imagery"), I.src,
          h("span", { class: "small muted" }, !state.bbox ? "Draw the area to see which free imagery covers it." :
            state.sources.length ? `${state.sources.length} free sources cover this box. ` : "Only your own imagery works here. ",
            state.bbox ? h("a", { href: `#/imagery?${qs({ bbox: state.bbox.join(","), case: state.case })}` }, "See every year as a table") : null)));
      if (I.src.value === "file" || !I.src.value) {
        details.push(h("div", { class: "field" }, h("label", { for: "n-after" }, "Image to search, path on this computer"), I.after),
          h("div", { class: "field" }, h("label", { for: "n-before" }, "Earlier image"), I.before),
          h("div", { class: "field" }, h("label", { for: "n-bands" }, "Band order"), I.bands));
      } else {
        const y = state.years;
        details.push(y === "loading" || y === null ? h("p", { class: "small muted" }, "Asking the service which years exist here…")
          : y.years.length ? h("div", { class: "grid2" }, h("div", { class: "field" }, h("label", { for: "n-ay" }, "Search year"), I.ay), h("div", { class: "field" }, h("label", { for: "n-by" }, "Compare with"), I.by))
          : h("p", { class: "small muted" }, y.error ? "Could not ask: " + y.error : "Nothing from this source here."));
      }
      details.push(h("label", { class: "check" }, I.mass, h("span", {}, "Look for mass graves and trenches instead of single graves")),
        h("div", { class: "field" }, h("label", { for: "n-osm" }, "OpenStreetMap extract, works offline"), I.osm));
    }
    fill(panel, 
      h("div", { class: "field" }, h("label", { for: "n-case" }, "Case"), I.caseSel),
      S.settings.place_search && !S.settings.offline_map ? h("div", { class: "field" }, h("label", { for: "n-q" }, "Find a place"),
        h("div", { class: "row", style: { flexWrap: "nowrap" } }, I.q, h("button", { class: "btn", type: "button", onclick: placeSearch }, "Go")), qres) : null,
      h("section", { style: { display: "flex", flexDirection: "column", gap: "10px" } }, stepHead(1, "Area", !!state.bbox),
        h("div", { class: "card pad" },
          h("div", { class: "row", style: { justifyContent: "space-between", alignItems: "flex-start" } },
            state.bbox ? h("div", {}, h("div", { style: { fontSize: "22px", fontWeight: 650 } }, fmtKm2(a)), h("div", { class: "small muted" }, `Box, ${fmtLen(sides[0])} × ${fmtLen(sides[1])}`))
              : h("div", { class: "small muted", style: { flex: 1 } }, "No area yet. Draw a box on the map."),
            h("button", { class: "btn", type: "button", onclick: () => drawer && drawer.start() }, icon("box"), state.bbox ? "Redraw" : "Draw box")),
          h("div", {}, h("div", { class: "row small muted", style: { justifyContent: "space-between" } }, h("span", {}, "Size limit for this search"),
            h("span", { class: "mono" }, `${state.bbox ? a.toFixed(a < 1 ? 2 : 1) : 0} / ${lim} km²`)),
            h("div", { class: "progress" + meterCls, style: { marginTop: "6px" } }, h("span", { style: { width: `${Math.max(state.bbox ? 2 : 0, Math.min(100, a / lim * 100))}%` } }))),
          state.bbox ? h("div", { class: "small muted mono" }, `${state.bbox[1].toFixed(4)}, ${state.bbox[0].toFixed(4)} → ${state.bbox[3].toFixed(4)}, ${state.bbox[2].toFixed(4)}`) : null)),
      h("section", { style: { display: "flex", flexDirection: "column", gap: "10px" } }, stepHead(2, "Look for"),
        h("div", { class: "kinds", role: "radiogroup", "aria-label": "What to look for" },
          kindCard("trash", "Dumping and burning", "New dumps, waste fires, scraped ground. Sentinel-2 at 10 m, up to 400 km²."),
          kindCard("burial_wide", "Burial, wide area", "Machine-dug pits, burning and fresh tracks after a disappearance."),
          kindCard("graves", "Grave search", "Grave-sized ground in drone or aerial photos, 3 cm to 1 m. A few hundred metres."))),
      h("section", { style: { display: "flex", flexDirection: "column", gap: "12px" } }, stepHead(3, state.kind === "graves" ? "Imagery" : "When"),
        h("div", { class: "field" }, h("label", { for: "n-name" }, "Name"), I.name), ...details),
      h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
        h("button", { class: "btn primary big", type: "button", onclick: run }, state.kind === "graves" ? "Search for graves" : "Run scan"),
        h("p", { class: "small muted", style: { margin: 0 } }, "Runs here, one job at a time. Downloads stay cached, so a re-run is quick. Leads, not evidence.")));
  }

  // Job tray over the map.
  let trayOpen = true;
  const trayBody = h("div", { class: "tray-body" }), trayCount = h("span", { class: "small muted" });
  const trayToggle = h("button", { class: "iconbtn", type: "button", "aria-label": "Collapse the job list", style: { border: 0 }, onclick: () => { trayOpen = !trayOpen; trayBody.hidden = !trayOpen; trayToggle.setAttribute("aria-label", trayOpen ? "Collapse the job list" : "Expand the job list"); } }, icon("down"));
  const tray = h("section", { class: "tray", "aria-label": "Jobs" },
    h("div", { class: "tray-head" }, h("h2", {}, "Jobs"), trayCount,
      h("button", { class: "btn small ghost", type: "button", style: { marginLeft: "auto" }, onclick: async () => { const r = await guard(() => post("/api/jobs/clear")); if (r) toast(r.cleared ? `Cleared ${r.cleared}. Files kept on disk.` : "Nothing finished to clear."); pollJobs(); } }, "Clear finished"),
      h("a", { class: "btn small ghost", href: "#/jobs" }, "All jobs"), trayToggle), trayBody);
  const drawTray = jobs => {
    const shown = jobs.slice(0, 6);
    const run = jobs.filter(j => j.status === "running").length, wait = jobs.filter(j => j.status === "queued").length;
    trayCount.textContent = run || wait ? `${run} running · ${wait} waiting` : `${jobs.length} recent`;
    fill(trayBody, ...(shown.length ? shown.map(j => jobRow(j)) : [h("div", { class: "empty small" }, "No jobs yet.")]));
    tray.hidden = !jobs.length;
  };
  mapwrap.append(tray);
  onJobs(drawTray); drawTray(S.jobs);

  draw();
  const page = h("div", { class: "workbench" }, panel, mapwrap);
  return after(page, () => {
    map = baseMap(mapEl, { zoomLevel: 3 });
    drawer = boxDrawer(map, setBox);
    cov = coverageLayer(map);
    waybackControl(map);
    const tools = L.control({ position: "topleft" });
    tools.onAdd = () => { const d = h("div", { class: "mapctl" }, h("button", { type: "button", onclick: () => drawer.start() }, icon("box"), "Draw box")); L.DomEvent.disableClickPropagation(d); return d; };
    tools.addTo(map);
    if (state.kind === "graves") cov.addTo(map);
    if (state.bbox) { setBox(state.bbox); map.fitBounds(bboxBounds(state.bbox).pad(0.3)); }
    else if (caseObj()) {
      const own = S.jobs.filter(j => j.case_id === state.case);
      if (own.length) map.fitBounds(own.map(j => bboxBounds(j.bbox)).reduce((x, y) => x.extend(y)).pad(0.3));
    }
    setTimeout(() => map.invalidateSize(), 50);
    return null;
  });
}

// Results of one search: leads, map with swipe, inspector, timeline -------------------------

async function renderJob(jid) {
  const job = await api(`/api/jobs/${jid}`);
  if (job.status !== "done") {
    const box = h("div", { class: "card", style: { overflow: "hidden" } }, jobRow(job, { log: true }));
    onJobs(jobs => { const j = jobs.find(x => x.id === jid); if (!j) return; if (j.status === "done") route(); else fill(box, jobRow(j)); });
    return h("div", { class: "page" }, h("div", { class: "crumbs" }, h("a", { href: "#/jobs" }, "Jobs"), " / ", job.name),
      h("h1", {}, job.name), h("p", { class: "sub" }, "Results show here when the search finishes."), box);
  }
  const [gj, layers, ts] = await Promise.all([file(jid, "candidates.geojson"), file(jid, "web/layers.json"), job.kind === "graves" ? null : file(jid, "timeseries.json")]);
  let review = await api(`/api/jobs/${jid}/review`);
  const feats = (gj && gj.features || []).filter(f => f.properties && f.properties.id);
  feats.forEach(f => { f.properties.kind = f.properties.kind || "grave"; });
  const byId = Object.fromEntries(feats.map(f => [f.properties.id, f]));
  const statusOf = id => (review[id] || {}).status || "unreviewed";
  const caseName = (S.cases || []).find(c => c.id === job.case_id);
  let filter = "unreviewed", sel = null;
  const kinds = [...new Set(feats.map(f => f.properties.kind))];
  const list = h("ul", { class: "list-plain", style: { gap: "6px" } }), pills = h("div", { class: "pills" }), reviewBox = h("div");
  const drawer = h("section", { class: "drawer", "aria-label": "Lead details" });
  const tl = h("section", { class: "timeline", "aria-label": "Pass timeline", hidden: !ts });
  const mapwrap = h("div", { class: "mapwrap" }), mapEl = h("div", { class: "map" }); mapwrap.append(mapEl);
  let map = null, shapes = {};

  const visible = () => feats.filter(f => filter === "all" || (filter === "unreviewed" ? statusOf(f.properties.id) === "unreviewed" : f.properties.kind === filter));
  function drawList() {
    const n = feats.length, st = feats.map(f => statusOf(f.properties.id)), done = st.filter(s => s !== "unreviewed").length;
    const seg = (k, c) => h("span", { style: { width: `${n ? st.filter(s => s === k).length / n * 100 : 0}%`, background: c } });
    fill(reviewBox, h("div", { class: "card pad" },
      h("div", { class: "row", style: { justifyContent: "space-between" } }, h("b", {}, `${done} of ${n} reviewed`),
        h("span", { class: "small muted" }, ["checked", "field_visit", "dismissed"].map(k => `${st.filter(s => s === k).length} ${REVIEW[k].toLowerCase()}`).join(" · "))),
      h("div", { class: "progress stack" }, seg("checked", "var(--ok)"), seg("field_visit", "var(--warn)"), seg("needs_imagery", "var(--warn)"), seg("dismissed", "var(--faint)"))));
    fill(pills, 
      h("button", { "aria-pressed": String(filter === "all"), onclick: () => { filter = "all"; drawList(); } }, `All ${n}`),
      h("button", { "aria-pressed": String(filter === "unreviewed"), onclick: () => { filter = "unreviewed"; drawList(); } }, `To review ${n - done}`),
      ...kinds.map(k => h("button", { "aria-pressed": String(filter === k), onclick: () => { filter = k; drawList(); } }, h("span", { class: "sw " + k }), `${LEAD[k]} ${feats.filter(f => f.properties.kind === k).length}`)));
    const rows = visible();
    fill(list, ...rows.map(f => {
      const p = f.properties, s = statusOf(p.id), closed = s === "dismissed" || s === "checked";
      return h("li", {}, h("button", { class: "leadrow" + (closed ? " closed" : ""), type: "button", "aria-current": String(sel === p.id), onclick: () => select(p.id, true) },
        h("span", { class: "sw " + p.kind }), h("span", {}, h("span", { class: "mono", style: { fontWeight: 500 } }, p.id), " ", h("span", { class: "small muted" }, LEAD[p.kind].toLowerCase())),
        h("span", { class: "score" }, fmtNum(p.score_mean !== undefined ? p.score_mean : p.evidence)),
        h("span", { class: "meta" }, p.kind === "grave" ? `${fmtNum(p.length_m)} × ${fmtNum(p.width_m)} m` + (p.access_m !== null && p.access_m !== undefined ? ` · ${p.access_m} m to a track` : "")
          : `${fmtInt(p.area_m2)} m² · ${p.landcover || ""}` + (p.first_seen ? ` · ${fmtDay(p.last_clear_look)} to ${fmtDay(p.first_seen)}` : "")),
        h("span", { class: "chip " + s, style: { gridColumn: "3" } }, REVIEW[s])));
    }));
    if (!rows.length) list.append(h("li", { class: "empty small" }, feats.length ? "Nothing here. Try All." : "This search found no leads."));
    styleShapes();
  }
  function styleShapes() {
    const C = COLORS();
    Object.entries(shapes).forEach(([id, lyr]) => {
      const p = byId[id].properties, s = statusOf(id), closed = s === "dismissed" || s === "checked";
      lyr.setStyle({ color: closed ? "#ffffff" : C[p.kind], weight: sel === id ? 4 : 2.5, dashArray: p.kind === "transient" || closed ? "6 4" : null,
                     fillColor: C[p.kind], fillOpacity: closed ? 0 : sel === id ? 0.35 : 0.2, opacity: closed ? 0.8 : 1 });
      if (sel === id) lyr.bringToFront();
    });
  }
  function select(id, pan) {
    sel = id;
    drawList(); drawDrawer(); drawTimeline();
    if (pan && map && shapes[id]) map.fitBounds(shapes[id].getBounds().pad(2.5), { maxZoom: 17 });
    const cur = list.querySelector('[aria-current="true"]'); if (cur) cur.scrollIntoView({ block: "nearest" });
  }
  async function decide(status, reason) {
    const r = await guard(() => post(`/api/jobs/${jid}/review`, { lead: sel, status, reason }));
    if (!r) return;
    review[sel] = r;
    const next = visible().find(f => f.properties.id !== sel && statusOf(f.properties.id) === "unreviewed");
    drawList(); drawDrawer();
    if (next && status !== "unreviewed") toast(`${sel}: ${REVIEW[status].toLowerCase()}. Next: ${next.properties.id}`);
  }
  function drawDrawer() {
    if (!sel) { fill(drawer, h("p", { class: "muted" }, feats.length ? "Pick a lead from the list or the map." : "No leads.")); return; }
    const p = byId[sel].properties, s = statusOf(sel), r = review[sel] || {};
    const isGrave = p.kind === "grave";
    const num = isGrave ? [[fmtNum(p.evidence), "evidence, robust z"], [fmtNum(p.shape_fit, 2), "grave shape, of 1"], [`${fmtNum(p.length_m)} × ${fmtNum(p.width_m)} m`, "size"]]
      : [[fmtNum(p.score_mean), "change score, robust z"], [`${Math.round((p.persistence || 0) * 100)}%`, "of clear looks since"], [`${fmtInt(p.area_m2)} m²`, p.landcover || "area"]];
    const reason = h("input", { class: "input", placeholder: "Why? e.g. construction site, badger sett", "aria-label": "Reason", value: r.reason || "" });
    const note = h("textarea", { class: "input", rows: "2", placeholder: "Add a note: what is it, who looked, when?", "aria-label": "Note" });
    const comp = !isGrave ? h("div", { class: "bars" }, h("h3", {}, "Why it scored"), Object.entries(COMPONENTS).map(([k, label]) => {
      const v = p[k]; if (v === undefined || v === null) return null;
      return h("div", { class: "bar" }, h("span", {}, label), h("span", { class: "track" }, h("span", { style: { width: `${Math.max(0, Math.min(100, v * 10))}%`, background: v >= 2 ? "var(--persistent)" : "var(--faint)" } })), h("span", { class: "mono" }, fmtNum(v)));
    })) : h("div", { class: "bars" }, h("h3", {}, "Why it ranks"), Object.keys(p).filter(k => k.startsWith("layer_")).sort((a, b) => p[b] - p[a]).map(k => {
      const v = p[k], name = GRAVE_LAYERS[k.slice(6)] || k.slice(6);
      return h("div", { class: "bar" }, h("span", { title: name }, k.slice(6)), h("span", { class: "track" }, h("span", { style: { width: `${Math.max(0, Math.min(100, v * 12))}%`, background: v >= 2 ? "var(--grave)" : "var(--faint)" } })), h("span", { class: "mono" }, fmtNum(v)));
    }));
    const img = h("img", { class: "chipimg", src: resultUrl(jid, `chips/${sel}.png`), alt: `Image clip of ${sel}: before, after and score`, loading: "lazy", onerror: e => e.target.replaceWith(h("p", { class: "small muted" }, "No image clip for this lead. Clips are made for the top ranked leads.")) });
    fill(drawer, 
      h("div", { class: "row", style: { flexWrap: "nowrap" } }, h("span", { class: "sw " + p.kind }), h("h2", { class: "mono", style: { fontSize: "18px" } }, sel),
        h("span", { class: "small muted" }, LEAD[p.kind]), h("span", { class: "chip " + s, style: { marginLeft: "auto" } }, REVIEW[s])),
      h("div", { class: "bignum" }, num.map(([v, k], i) => h("div", {}, h("div", { class: i ? "m" : "n" }, v), h("div", { class: "k" }, k)))),
      img,
      ts && ts.leads[sel] ? leadChart(ts, sel) : null,
      comp,
      h("dl", { class: "facts" },
        !isGrave && p.first_seen ? [h("dt", {}, p.kind === "transient" ? "Bare" : "Appeared"), h("dd", {}, p.kind === "transient" && p.last_seen ? `${fmtDay(p.first_seen)} to ${fmtDay(p.last_seen)}` : `between ${fmtDay(p.last_clear_look)} and ${fmtDay(p.first_seen)}`)] : null,
        isGrave && p.access_m !== null && p.access_m !== undefined ? [h("dt", {}, "To a track"), h("dd", {}, `${p.access_m} m`)] : null,
        h("dt", {}, "Location"), h("dd", { class: "row", style: { gap: "8px" } }, h("span", { class: "mono" }, `${(+p.lat).toFixed(6)}, ${(+p.lon).toFixed(6)}`),
          h("button", { class: "iconbtn", type: "button", "aria-label": "Copy coordinates", style: { width: "28px", height: "28px" }, onclick: () => copyText(`${p.lat}, ${p.lon}`) }, icon("copy")),
          p.maps_url ? h("a", { href: p.maps_url, target: "_blank", rel: "noopener" }, "Google Maps") : null)),
      h("div", { class: "field" }, h("span", { class: "label" }, "Decision"),
        h("div", { class: "row", style: { gap: "6px" } },
          h("button", { class: "btn primary", type: "button", onclick: () => decide("checked", reason.value) }, "Checked  C"),
          h("button", { class: "btn", type: "button", onclick: () => decide("field_visit", reason.value) }, "Field visit"),
          h("button", { class: "btn", type: "button", onclick: () => decide("needs_imagery", reason.value) }, "Needs imagery"),
          h("button", { class: "btn", type: "button", onclick: () => decide("dismissed", reason.value) }, "Dismiss  D"),
          s !== "unreviewed" ? h("button", { class: "btn ghost small", type: "button", onclick: () => decide("unreviewed", "") }, "Undo") : null),
        reason),
      h("div", { class: "notes" }, (r.notes || []).map(n => h("div", { class: "note" }, h("div", { class: "small muted" }, fmtWhen(n.time)), n.text)), note,
        h("button", { class: "btn small", type: "button", onclick: async () => { const x = await guard(() => post(`/api/jobs/${jid}/review`, { lead: sel, note: note.value })); if (x) { review[sel] = x; drawDrawer(); } } }, "Add note")),
      h("div", { class: "row" }, h("a", { class: "btn", href: `#/job/${jid}/lead/${sel}` }, icon("print"), "Field sheet")));
  }
  function drawTimeline() {
    if (!ts) return;
    fill(tl, passStrip(ts, sel));
  }

  // Map layers: imagery overlays, heat, swipe.
  const overlays = {}, layerBox = h("div", { class: "layerbox" });
  let swipeX = null;
  const line = h("div", { class: "swipe-line", hidden: true }, h("div", { class: "swipe-handle", title: "Drag to compare" }, icon("swipe")));
  const tagL = h("div", { class: "swipe-tag", hidden: true }), tagR = h("div", { class: "swipe-tag", hidden: true });
  mapwrap.append(line, tagL, tagR, layerBox);
  const show = { image: layers && layers.layers.some(l => l.id === "after") ? "after" : null, swipe: false, heat: {}, opacity: 0.7 };
  function applyLayers() {
    if (!map || !layers) return;
    Object.entries(overlays).forEach(([id, lyr]) => {
      const meta = layers.layers.find(l => l.id === id);
      const on = meta.kind === "heat" ? !!show.heat[id] : (show.swipe ? (id === "before" || id === "after") : show.image === id);
      if (on && !map.hasLayer(lyr)) lyr.addTo(map); if (!on && map.hasLayer(lyr)) map.removeLayer(lyr);
      if (meta.kind === "heat") lyr.setOpacity(show.opacity);
    });
    const two = show.swipe && overlays.before && overlays.after;
    line.hidden = tagL.hidden = tagR.hidden = !two;
    if (two) {
      if (swipeX === null) swipeX = mapEl.clientWidth / 2;
      tagL.textContent = layers.layers.find(l => l.id === "before").label; tagR.textContent = layers.layers.find(l => l.id === "after").label;
      tagL.style.left = "10px"; tagR.style.right = "10px";
    }
    clip();
  }
  function clip() {
    const two = show.swipe && overlays.before && overlays.after && map.hasLayer(overlays.after);
    ["before", "after"].forEach(id => { const el = overlays[id] && overlays[id].getElement(); if (el) el.style.clipPath = ""; });
    if (!two) return;
    line.style.left = `${swipeX}px`;
    const b = L.latLngBounds(layers.bounds), nw = map.latLngToContainerPoint(b.getNorthWest()), se = map.latLngToContainerPoint(b.getSouthEast());
    overlays.after.getElement().style.clipPath = `inset(0 0 0 ${Math.max(0, swipeX - nw.x)}px)`;
    overlays.before.getElement().style.clipPath = `inset(0 ${Math.max(0, se.x - swipeX)}px 0 0)`;
  }
  const handle = line.firstChild;
  handle.addEventListener("pointerdown", e => {
    handle.setPointerCapture(e.pointerId); map.dragging.disable();
    const move = ev => { const r = mapEl.getBoundingClientRect(); swipeX = Math.max(0, Math.min(r.width, ev.clientX - r.left)); clip(); };
    const up = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); map.dragging.enable(); };
    handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up);
  });
  function drawLayerBox() {
    if (!layers) { fill(layerBox); return; }
    const imgs = layers.layers.filter(l => l.kind === "image"), heats = layers.layers.filter(l => l.kind === "heat");
    const box = h("div", { class: "legend" }, h("b", {}, "Layers"),
      imgs.map(l => h("label", { class: "check" }, h("input", { type: "radio", name: "img", checked: !show.swipe && show.image === l.id, onchange: () => { show.image = l.id; show.swipe = false; applyLayers(); drawLayerBox(); } }), h("span", {}, l.label))),
      h("label", { class: "check" }, h("input", { type: "radio", name: "img", checked: !show.swipe && !show.image, onchange: () => { show.image = null; show.swipe = false; applyLayers(); drawLayerBox(); } }), h("span", {}, "Base map only")),
      imgs.length === 2 ? h("label", { class: "check" }, h("input", { type: "radio", name: "img", checked: show.swipe, onchange: () => { show.swipe = true; applyLayers(); drawLayerBox(); } }), h("span", {}, h("b", {}, "Swipe"), " before | after")) : null,
      heats.map(l => h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!show.heat[l.id], onchange: e => { show.heat[l.id] = e.target.checked; applyLayers(); } }), h("span", {}, `${l.label}, above ${l.threshold}`))),
      heats.length ? h("label", { class: "small muted" }, "Heat opacity", h("input", { type: "range", min: "0", max: "100", value: String(show.opacity * 100), style: { width: "100%", accentColor: "var(--accent)" }, oninput: e => { show.opacity = e.target.value / 100; applyLayers(); } })) : null);
    L.DomEvent.disableClickPropagation(box); L.DomEvent.disableScrollPropagation(box);
    fill(layerBox, box);
  }

  const keys = e => {
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
    const rows = visible().map(f => f.properties.id), i = rows.indexOf(sel);
    if (e.key === "j" || e.key === "ArrowDown") { if (rows.length) select(rows[Math.min(rows.length - 1, i + 1)], true); e.preventDefault(); }
    else if (e.key === "k" || e.key === "ArrowUp") { if (rows.length) select(rows[Math.max(0, i - 1)], true); e.preventDefault(); }
    else if (e.key === "c" && sel) decide("checked", "");
    else if (e.key === "d" && sel) decide("dismissed", "");
    else if (e.key === "s" && overlays.before && overlays.after) { show.swipe = !show.swipe; applyLayers(); drawLayerBox(); }
  };
  document.addEventListener("keydown", keys); onLeave(() => document.removeEventListener("keydown", keys));

  const header = h("div", { class: "card pad" },
    h("div", { class: "crumbs" }, caseName ? [h("a", { href: `#/case/${caseName.id}` }, caseName.name), " / "] : [h("a", { href: "#/jobs" }, "Jobs"), " / "], "Results"),
    h("h1", { style: { fontSize: "19px" } }, job.name),
    h("div", { class: "small muted" }, [KIND[job.kind], job.params.start ? `${fmtDay(job.params.start)} to ${fmtDay(job.params.end)}` : null,
      job.params.since ? `since ${fmtDay(job.params.since)}` : null, job.params.after_year ? `${job.params.source} ${job.params.after_year}` + (job.params.before_year ? ` vs ${job.params.before_year}` : "") : null,
      fmtKm2(areaKm2(job.bbox)), `finished ${fmtWhen(job.finished)}`].filter(Boolean).join(" · ")),
    h("div", { class: "row", style: { gap: "6px" } },
      h("a", { class: "btn small", href: resultUrl(jid, "report.html"), target: "_blank", rel: "noopener" }, "Report", icon("ext")),
      h("a", { class: "btn small", href: resultUrl(jid, "candidates.geojson"), download: "" }, "GeoJSON"),
      h("a", { class: "btn small", href: resultUrl(jid, "candidates.kml"), download: "" }, "KML"),
      job.kind === "graves" ? h("a", { class: "btn small", href: resultUrl(jid, "candidates.gpx"), download: "" }, "GPX") : null),
    !job.case_id ? h("div", { class: "field" }, h("label", { class: "label", for: "to-case" }, "Add to a case"),
      h("select", { class: "input", id: "to-case", onchange: async e => { if (!e.target.value) return; const r = await guard(() => post(`/api/jobs/${jid}/case`, { case_id: e.target.value })); if (r) { toast("Added to the case."); route(); } } },
        h("option", { value: "" }, "Choose a case…"), (S.cases || []).filter(c => c.status !== "closed").map(c => h("option", { value: c.id }, c.name)))) : null);

  drawList(); drawDrawer(); drawTimeline(); drawLayerBox();
  const page = h("div", { class: "results" },
    h("aside", { class: "leads", "aria-label": "Leads" }, header, reviewBox, pills, list,
      h("p", { class: "small muted", style: { margin: 0 } }, "Keys: J and K move through the list, C marks checked, D dismisses, S toggles the swipe.")),
    mapwrap, drawer, tl);
  if (!S.cases) api("/api/cases").then(d => { S.cases = d.cases; }).catch(() => {});
  return after(page, () => {
    map = baseMap(mapEl, { zoomLevel: 3 });
    if (layers) {
      layers.layers.forEach(l => { overlays[l.id] = L.imageOverlay(resultUrl(jid, l.file), layers.bounds, { opacity: l.kind === "heat" ? show.opacity : 1, className: l.kind === "heat" ? "heat" : "", interactive: false }); });
      applyLayers();
      map.on("move zoom resize zoomend moveend viewreset", clip);
      Object.values(overlays).forEach(o => o.on("load", clip));
    }
    L.rectangle(bboxBounds(job.bbox), { color: "#fff", weight: 2, dashArray: "8 6", fill: false, interactive: false }).addTo(map);
    const group = L.featureGroup().addTo(map);
    feats.forEach(f => {
      const lyr = L.geoJSON(f, { style: {} }).getLayers()[0];
      if (!lyr) return;
      lyr.on("click", () => select(f.properties.id, false)).bindTooltip(esc(`${f.properties.id} · ${LEAD[f.properties.kind]}`)).addTo(group);
      shapes[f.properties.id] = lyr;
    });
    styleShapes();
    map.fitBounds(group.getLayers().length ? group.getBounds().pad(0.3) : bboxBounds(job.bbox).pad(0.1));
    const first = visible()[0] || feats[0];
    if (first) select(first.properties.id, false);
    setTimeout(() => { map.invalidateSize(); clip(); }, 60);
    return null;
  });
}

/* Score of one lead on every clear pass, with the target window and the threshold. */
function leadChart(ts, id) {
  const pts = ts.leads[id] || []; if (!pts.length) return null;
  const W = 340, H = 130, x0 = 24, x1 = W - 6, y0 = 10, y1 = H - 22;
  const t = d => new Date(d).getTime();
  const all = ts.passes.map(p => t(p.date)), lo = Math.min(...all), hi = Math.max(...all, lo + 1);
  const vals = pts.map(p => p[1]), vmin = Math.min(-1, ...vals), vmax = Math.max(9, ...vals);
  const X = d => x0 + (t(d) - lo) / (hi - lo) * (x1 - x0), Y = v => y1 - (v - vmin) / (vmax - vmin) * (y1 - y0);
  const kind = id.startsWith("T") ? "transient" : "persistent", thr = kind === "transient" ? ts.min_event_score : ts.min_score;
  const C = COLORS(), years = [...new Set(ts.passes.map(p => p.date.slice(0, 4)))];
  return h("figure", { style: { margin: 0 } },
    h("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Score of ${id} on every clear pass`, style: { width: "100%", display: "block" } },
      h("rect", { x: X(ts.target_start), y: y0, width: Math.max(2, X(ts.target_end) - X(ts.target_start)), height: y1 - y0, fill: C[kind], opacity: "0.08" }),
      h("line", { x1: x0, x2: x1, y1: Y(0), y2: Y(0), stroke: "var(--line)" }),
      h("line", { x1: x0, x2: x1, y1: Y(thr), y2: Y(thr), stroke: C[kind], "stroke-dasharray": "4 3" }),
      pts.map(([d, v]) => h("circle", { cx: X(d), cy: Y(v), r: "2.8", fill: v >= thr && t(d) >= t(ts.target_start) ? C[kind] : "var(--faint)" })),
      h("text", { x: 2, y: Y(thr) + 4, "font-size": "10", fill: "var(--muted)" }, String(thr)),
      years.map(y => h("text", { x: X(`${y}-07-01`) - 12, y: H - 6, "font-size": "10", fill: "var(--muted)" }, y))),
    h("figcaption", { class: "small muted" }, `Score on every clear pass. Shaded: the search window. Dashed: threshold ${thr}.`));
}

/* Every pass as a strip: hazy passes boxed, the selected lead's score as dots. */
function passStrip(ts, id) {
  const W = 1200, H = 96, x0 = 8, x1 = W - 8;
  const t = d => new Date(d).getTime();
  const all = ts.passes.map(p => t(p.date)), lo = Math.min(...all), hi = Math.max(...all, lo + 1);
  const X = d => x0 + (t(d) - lo) / (hi - lo) * (x1 - x0);
  const pts = Object.fromEntries((ts.leads[id] || []).map(([d, v]) => [d, v]));
  const vals = Object.values(pts), vmax = Math.max(9, ...vals), vmin = Math.min(-1, ...vals);
  const Y = v => 84 - (v - vmin) / (vmax - vmin) * 64;
  const kind = id && id.startsWith("T") ? "transient" : "persistent", thr = kind === "transient" ? ts.min_event_score : ts.min_score, C = COLORS();
  const bad = ts.passes.filter(p => p.bad).length;
  return h("div", {},
    h("div", { class: "row small muted", style: { gap: "14px" } }, h("b", { style: { color: "var(--ink)" } }, `${ts.passes.length} passes`), id ? `score of ${id} per pass` : "pick a lead",
      h("span", { class: "row", style: { gap: "6px" } }, h("span", { style: { width: "9px", height: "9px", borderRadius: "50%", background: C[kind] } }), "above threshold"),
      bad ? h("span", { class: "row", style: { gap: "6px" } }, h("span", { style: { width: "10px", height: "10px", border: "1.5px solid var(--danger)" } }), `${bad} hazy passes, left out of dig detection`) : null),
    h("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", "aria-label": "Passes over time", style: { width: "100%", height: "96px", display: "block" } },
      h("rect", { x: X(ts.target_start), y: 0, width: Math.max(2, X(ts.target_end) - X(ts.target_start)), height: H, fill: C[kind], opacity: "0.07" }),
      h("line", { x1: x0, x2: x1, y1: Y(thr), y2: Y(thr), stroke: C[kind], "stroke-dasharray": "5 4", opacity: "0.7" }),
      ts.passes.map(p => [h("line", { x1: X(p.date), x2: X(p.date), y1: 88, y2: 94, stroke: p.target ? "var(--ink)" : "var(--faint)" }),
        p.bad ? h("rect", { x: X(p.date) - 4, y: 14, width: 8, height: 76, fill: "none", stroke: "var(--danger)", "stroke-width": "1.5" }) : null,
        pts[p.date] !== undefined ? h("circle", { cx: X(p.date), cy: Y(pts[p.date]), r: "3.5", fill: pts[p.date] >= thr && p.target ? C[kind] : "var(--faint)" }, h("title", {}, `${p.date}: ${pts[p.date]}`)) : null])));
}

// One lead and its field sheet --------------------------------------------------------------

async function renderLead(jid, lid) {
  const [job, gj] = await Promise.all([api(`/api/jobs/${jid}`), file(jid, "candidates.geojson")]);
  let review = await api(`/api/jobs/${jid}/review`);
  if (!S.cases) S.cases = (await api("/api/cases")).cases;
  const feats = (gj && gj.features || []).filter(f => f.properties && f.properties.id);
  const i = feats.findIndex(f => f.properties.id === lid);
  if (i < 0) throw new Error(`No lead ${lid} in this search.`);
  const f = feats[i], p = f.properties; p.kind = p.kind || "grave";
  const cs = S.cases.find(c => c.id === job.case_id);
  const isGrave = p.kind === "grave", grim = isGrave || (cs && cs.kind === "missing_person");
  const prev = feats[i - 1], next = feats[i + 1];
  const decision = h("div"), notes = h("div", { class: "notes" });
  const reason = h("input", { class: "input", placeholder: "Reason, shown in the case log", "aria-label": "Reason" });
  const note = h("textarea", { class: "input", rows: "2", placeholder: "Add a note for the case log", "aria-label": "Note" });
  const st = () => (review[lid] || {}).status || "unreviewed";
  async function save(body) { const r = await guard(() => post(`/api/jobs/${jid}/review`, { lead: lid, ...body })); if (r) { review[lid] = r; drawDecision(); } }
  function drawDecision() {
    const s = st();
    fill(decision, h("div", { class: "row", style: { gap: "8px" } },
      [["field_visit", "Plan a field visit"], ["checked", "Checked"], ["needs_imagery", "Needs better imagery"], ["dismissed", "Dismiss"]].map(([k, t]) =>
        h("button", { class: "btn" + (s === k ? " primary" : ""), type: "button", "aria-pressed": String(s === k), onclick: () => save({ status: k, reason: reason.value }) }, s === k ? icon("check") : null, t)),
      h("span", { class: "chip " + s }, REVIEW[s])));
    fill(notes, ...((review[lid] || {}).notes || []).map(n => h("div", { class: "note" }, h("div", { class: "small muted" }, fmtWhen(n.time)), n.text)));
  }
  drawDecision();
  const why = isGrave ? Object.keys(p).filter(k => k.startsWith("layer_") && p[k] >= 1.5).sort((a, b) => p[b] - p[a]).map(k => `${GRAVE_LAYERS[k.slice(6)] || k.slice(6)} (${fmtNum(p[k])})`)
    : Object.entries(COMPONENTS).filter(([k]) => p[k] >= 1.5).sort((a, b) => p[b[0]] - p[a[0]]).map(([k, t]) => `${t[0].toUpperCase() + t.slice(1)} (${fmtNum(p[k])})`);
  if (isGrave && p.access_m !== null && p.access_m !== undefined && p.access_m <= 50) why.push(`A vehicle can get within ${p.access_m} m`);
  const lat = (+p.lat).toFixed(6), lon = (+p.lon).toFixed(6);
  const facts = isGrave
    ? [["Size", `${fmtNum(p.length_m)} × ${fmtNum(p.width_m)} m, long side ${p.orientation_deg}°`], ["Evidence", `${fmtNum(p.evidence)} mean, ${fmtNum(p.evidence_max)} peak`], ["Grave shape", `${fmtNum(p.shape_fit, 2)} of 1`],
       ["To a track", p.access_m === null || p.access_m === undefined ? "no map data" : `${p.access_m} m`], ["Location", `${lat}, ${lon}`]]
    : [["Score", `${fmtNum(p.score_mean)} mean, ${fmtNum(p.score_max)} peak`], ["Area", `${fmtInt(p.area_m2)} m², ${p.landcover || ""}`], ["Persistence", `${Math.round((p.persistence || 0) * 100)}% of clear looks`],
       [p.kind === "transient" ? "Bare" : "Appeared", p.kind === "transient" && p.last_seen ? `${fmtDay(p.first_seen)} to ${fmtDay(p.last_seen)}` : `between ${fmtDay(p.last_clear_look)} and ${fmtDay(p.first_seen)}`], ["Location", `${lat}, ${lon}`]];
  const checklist = grim
    ? ["Approach along the track, never across the patch", "Photograph from four sides with a scale bar", "Note soil colour, plants and smell", "Mark it with tape and a pin. Do not probe, do not dig.",
       `Call the forensic team${S.settings.field_contact ? ": " + S.settings.field_contact : ""}`]
    : ["Do not touch or move the material. It can be hazardous.", "Photograph from a distance, with something for scale", "Note what it looks like: rubble, plastic, drums, ash, smell, smoke",
       "Note vehicle tracks and how one gets in", `Report it${S.settings.field_contact ? ": " + S.settings.field_contact : ""}`];
  const mini = h("div", { class: "minimap" });
  const gpx = () => download(`${lid}.gpx`, `<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Disturbed Ground Detector" xmlns="http://www.topografix.com/GPX/1/1">\n<wpt lat="${lat}" lon="${lon}"><name>${esc(lid)}</name><desc>${esc(`${job.name}: ${LEAD[p.kind]}. A lead, not evidence.`)}</desc></wpt>\n</gpx>\n`, "application/gpx+xml");
  const page = h("div", { class: "page" },
    h("div", { class: "crumbs" }, cs ? [h("a", { href: `#/case/${cs.id}` }, cs.name), " / "] : null, h("a", { href: `#/job/${jid}` }, job.name), " / ", lid),
    h("div", { class: "leadpage" },
      h("div", { class: "main" },
        h("div", { class: "row" }, h("h1", { class: "mono" }, lid), h("span", { class: "chip " + (isGrave ? "solid-grave" : "") }, LEAD[p.kind]),
          h("span", { class: "muted" }, `rank ${p.rank} of ${feats.filter(x => (x.properties.kind || "grave") === p.kind).length}`),
          h("span", { style: { marginLeft: "auto" }, class: "row" },
            prev ? h("a", { class: "iconbtn", href: `#/job/${jid}/lead/${prev.properties.id}`, "aria-label": "Previous lead" }, icon("left")) : null,
            next ? h("a", { class: "iconbtn", href: `#/job/${jid}/lead/${next.properties.id}`, "aria-label": "Next lead" }, icon("right")) : null)),
        grim ? h("div", { class: "banner danger" }, icon("alert"), h("span", {}, h("b", {}, "Do not dig. "), "If it looks like a grave on the ground: stop, mark it, keep people and dogs off it, photograph it, and call the forensic team.")) : null,
        h("img", { class: "chipimg", src: resultUrl(jid, `chips/${lid}.png`), alt: `Image clips of ${lid}`, onerror: e => e.target.replaceWith(h("p", { class: "small muted" }, "No image clip for this lead.")) }),
        h("div", { class: "split", style: { gap: "16px" } },
          h("dl", { class: "facts card pad main", style: { display: "grid", flex: "1 1 260px" } }, facts.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
          h("div", { class: "card pad aside", style: { flex: "1 1 260px" } }, h("h2", {}, "Why it ranks here"),
            why.length ? h("ul", { style: { margin: 0, paddingLeft: "18px" } }, why.map(w => h("li", {}, w))) : h("p", { class: "small muted", style: { margin: 0 } }, "No single strong reason. A weak lead."))),
        h("section", { class: "card pad", "aria-labelledby": "dec-h" }, h("h2", { id: "dec-h" }, "Decision"), decision, reason, notes, note,
          h("button", { class: "btn small", type: "button", onclick: () => { if (note.value.trim()) save({ note: note.value }).then(() => { note.value = ""; }); } }, "Add note"))),
      h("section", { class: "sheetwrap", "aria-labelledby": "fs-h" },
        h("div", { class: "row" }, h("h2", { id: "fs-h", style: { marginRight: "auto" } }, "Field sheet"),
          h("button", { class: "btn small", type: "button", onclick: () => window.print() }, icon("print"), "Print"),
          h("button", { class: "btn small", type: "button", onclick: gpx }, icon("down"), "GPX"),
          p.maps_url ? h("a", { class: "btn small", href: p.maps_url, target: "_blank", rel: "noopener" }, "Maps", icon("ext")) : null),
        h("article", { class: "sheet", "aria-label": "Field sheet preview" },
          h("div", { class: "top" }, h("span", {}, `${cs ? cs.name : job.name} · field sheet`), h("span", { class: "mono" }, lid)),
          h("div", { class: "warnbox" }, grim ? "DO NOT DIG. Stop, mark it, keep people and dogs off, photograph, call the forensic team."
            : "DO NOT TOUCH. Dumped waste can be toxic, sharp or still burning."),
          mini,
          h("div", {}, h("div", { style: { fontSize: "10.5px", color: "#4D5966" } }, "Location, WGS84"), h("div", { class: "coords" }, `${lat}, ${lon}`),
            h("div", { style: { color: "#4D5966" } }, isGrave ? `${fmtNum(p.length_m)} × ${fmtNum(p.width_m)} m, long side ${p.orientation_deg}° from north.` : `${fmtInt(p.area_m2)} m², ${LEAD[p.kind].toLowerCase()}.`,
              ` geo:${lat},${lon}`)),
          h("ul", {}, checklist.map(c => h("li", {}, c))),
          h("div", { class: "foot" }, `A lead, not evidence. From search "${job.name}" (${job.id}), Disturbed Ground Detector ${window.DGD.version}. Inputs and file hashes: manifest.json in that search's folder.`)))));
  return after(page, () => {
    const map = baseMap(mini, { zoomLevel: 17, zoom: false, layers: false, scale: true, center: [+p.lat, +p.lon] });
    const C = COLORS();
    const shape = L.geoJSON(f, { style: { color: C[p.kind], weight: 3, fillOpacity: 0.25 } }).addTo(map);
    L.circle([+p.lat, +p.lon], { radius: isGrave ? 12 : 60, color: C[p.kind], weight: 1.5, fill: false }).addTo(map);
    map.fitBounds(shape.getBounds().pad(isGrave ? 6 : 1.5), { maxZoom: 19 });
    setTimeout(() => map.invalidateSize(), 50);
    return null;
  });
}

// Jobs and logs ---------------------------------------------------------------------

async function renderJobs(jid) {
  const queue = h("aside", { class: "queue", "aria-label": "Job queue" }), detail = h("section", { class: "detail", "aria-label": "Job detail" });
  let current = jid || null, full = null, follow = true, warnOnly = false, timer = null, logSection = null;
  const logPre = h("pre", { class: "log", "aria-live": "off" }), top = h("div", { style: { display: "flex", flexDirection: "column", gap: "14px" } });
  function drawQueue(jobs) {
    if (!current && jobs.length) current = (jobs.find(j => BUSY.includes(j.status)) || jobs[0]).id;
    const groups = [["Running", jobs.filter(j => j.status === "running" || j.status === "stopping")], ["Waiting", jobs.filter(j => j.status === "queued")],
                    ["Finished", jobs.filter(j => !BUSY.includes(j.status))]];
    fill(queue, ...groups.filter(([, js]) => js.length).map(([t, js]) => h("section", { style: { display: "flex", flexDirection: "column", gap: "6px" } },
      h("h2", { class: "qhead" }, t),
      js.map(j => h("button", { class: "qitem", type: "button", "aria-current": String(j.id === current), onclick: () => { location.hash = `#/jobs/${j.id}`; } },
        h("span", { class: "row", style: { justifyContent: "space-between", flexWrap: "nowrap" } }, h("b", { style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, j.name), h("span", { class: "chip " + j.status }, j.status)),
        BUSY.includes(j.status) ? progressBar(jobFraction(j)) : null,
        h("span", { class: "small muted" }, `${KIND[j.kind]} · ${fmtWhen(j.created)}`),
        h("span", { class: "small" + (j.status === "failed" ? "" : " muted"), style: j.status === "failed" ? { color: "var(--danger)" } : null }, jobLine(j)))))),
      jobs.some(j => !BUSY.includes(j.status)) ? h("button", { class: "btn small", type: "button", onclick: async () => { await guard(() => post("/api/jobs/clear")); pollJobs(); } }, "Clear finished") : null);
    if (!jobs.length) queue.append(h("p", { class: "muted" }, "No jobs yet. ", h("a", { href: "#/new" }, "Start a search")));
  }
  function drawLog() {
    const lines = (full.log || []).filter(l => !warnOnly || /WARN|ERROR|Traceback|Error/.test(l));
    fill(logPre, ...lines.map(l => h("div", { class: /ERROR|Traceback|Error/.test(l) ? "e" : /WARN/.test(l) ? "w" : "" }, l)));
    if (!lines.length) add(logPre, h("div", { style: { opacity: 0.6 } }, warnOnly ? "No warnings." : "Nothing logged yet."));
    if (follow) logPre.scrollTop = logPre.scrollHeight;
  }
  async function loadDetail() {
    clearTimeout(timer);
    if (!current) { fill(detail, h("div", { class: "empty" }, "Pick a job.")); return; }
    try { full = await api(`/api/jobs/${current}`); } catch (e) { fill(detail, h("div", { class: "banner danger" }, e.message)); return; }
    const j = full, p = j.progress || {};
    const steps = (j.steps || []).map((label, i) => {
      const isCur = BUSY.includes(j.status) && i === j.steps.length - 1;
      return h("li", {}, isCur ? icon("spin") : icon("check"), h("span", { style: isCur ? { fontWeight: 600 } : null }, label),
        h("span", { class: "mono small", style: { color: isCur ? "var(--accent)" : "var(--muted)" } }, isCur && p.of ? `${p.done} / ${p.of}` : ""),
        isCur && p.of ? h("span", { style: { gridColumn: "2 / 4" } }, progressBar(p.done / p.of)) : null);
    });
    const pending = Math.max(0, (p.total || 0) - (j.steps || []).length);
    if (BUSY.includes(j.status) && pending) steps.push(h("li", { class: "pending" }, h("span", { class: "bullet" }), `${pending} more step${pending > 1 ? "s" : ""}`, ""));
    if (j.status === "done") steps.push(h("li", {}, icon("checkc"), h("b", {}, "Finished"), h("span", { class: "small muted" }, fmtWhen(j.finished))));
    if (!logSection) {
      logSection = h("section", { "aria-labelledby": "log-h" },
        h("div", { class: "loghead" }, h("h2", { id: "log-h" }, "Log"),
          h("label", { class: "check small" }, h("input", { type: "checkbox", checked: warnOnly, onchange: e => { warnOnly = e.target.checked; drawLog(); } }), "warnings only"),
          h("label", { class: "check small" }, h("input", { type: "checkbox", checked: follow, onchange: e => { follow = e.target.checked; } }), "follow"),
          h("button", { class: "btn small ghost", type: "button", style: { marginLeft: "auto" }, onclick: () => download(`${full.id}.log`, (full.log || []).join("\n") + "\n", "text/plain") }, icon("down"), "Save log")),
        logPre);
      fill(detail, top, logSection);
    }
    fill(top, 
      h("div", { class: "page-head" }, h("div", { class: "grow" }, h("div", { class: "row" }, h("h1", { style: { fontSize: "20px" } }, j.name), h("span", { class: "chip " + j.status }, j.status)),
        h("p", { class: "sub" }, [KIND[j.kind], j.started ? `started ${fmtWhen(j.started)}` : `queued ${fmtWhen(j.created)}`, j.case_id && S.cases ? (S.cases.find(c => c.id === j.case_id) || {}).name : null].filter(Boolean).join(" · "))),
        j.status === "queued" || j.status === "running" ? h("button", { class: "btn danger", type: "button", onclick: () => jobAction(j.id, "stop") }, icon("stop"), j.status === "queued" ? "Cancel" : "Stop") : null,
        j.status === "done" ? h("a", { class: "btn primary", href: `#/job/${j.id}` }, "Review leads") : null,
        j.status === "done" ? h("a", { class: "btn", href: resultUrl(j.id, "report.html"), target: "_blank", rel: "noopener" }, "Report", icon("ext")) : null,
        !BUSY.includes(j.status) ? h("button", { class: "btn", type: "button", onclick: async () => { await guard(() => post(`/api/jobs/${j.id}/remove`)); current = null; location.hash = "#/jobs"; } }, "Remove from list") : null),
      BUSY.includes(j.status) ? h("div", { class: "card pad" }, h("div", { class: "row", style: { justifyContent: "space-between" } }, h("b", {}, jobLine(j)), h("span", { class: "mono" }, `${Math.round(jobFraction(j) * 100)}%`)), progressBar(jobFraction(j), true)) : null,
      j.error && j.status !== "done" ? h("div", { class: "banner danger" }, icon("alert"), j.error) : null,
      j.command ? h("div", { class: "field" }, h("div", { class: "row", style: { justifyContent: "space-between" } }, h("span", { class: "label" }, "The exact command this job runs"),
        h("button", { class: "btn small ghost", type: "button", onclick: () => copyText(j.command) }, icon("copy"), "Copy")), h("pre", { class: "cmd" }, j.command)) : null,
      steps.length ? h("section", { class: "card pad" }, h("h2", {}, "Steps"), h("ol", { class: "steps" }, steps)) : null);
    drawLog();
    if (BUSY.includes(j.status)) timer = setTimeout(loadDetail, 1500);
  }
  onLeave(() => clearTimeout(timer));
  onJobs(jobs => drawQueue(jobs));
  drawQueue(S.jobs);
  loadDetail();
  return h("div", { class: "jobsplit" }, queue, detail);
}

// Imagery availability -------------------------------------------------------------------

async function renderImagery(q) {
  const P = params(q);
  if (!S.cases) S.cases = (await api("/api/cases")).cases;
  const st = { bbox: P.bbox ? P.bbox.split(",").map(Number) : null, sources: [], years: {}, pick: null, s2: null, wayback: null };
  const panel = h("aside", { class: "panel", "aria-label": "Plan a grave search" }), body = h("div", { class: "page", style: { maxWidth: "none" } });
  const mapEl = h("div", { style: { height: "220px", borderRadius: "10px", overflow: "hidden" } });
  let map = null, rect = null, drawer = null;
  const I = { name: h("input", { class: "input", id: "im-name", placeholder: "e.g. Case 114, North woods" }),
    caseSel: h("select", { class: "input", id: "im-case" }, h("option", { value: "" }, "No case"), S.cases.filter(c => c.status !== "closed").map(c => h("option", { value: c.id, selected: c.id === P.case }, c.name))),
    mass: h("input", { type: "checkbox", id: "im-mass" }), osm: h("input", { class: "input mono", id: "im-osm", placeholder: "/path/to/region.osm.pbf (optional)" }) };

  async function load() {
    st.sources = []; st.years = {}; st.pick = null; st.s2 = null; draw();
    if (!st.bbox) return;
    st.sources = await api(`/api/sources?bbox=${st.bbox.join(",")}`).catch(() => []);
    draw();
    await Promise.all(st.sources.map(async s => {
      st.years[s.key] = "loading"; draw();
      const r = await api(`/api/source_years?source=${s.key}&bbox=${st.bbox.join(",")}`).catch(e => ({ years: [], error: e.message }));
      st.years[s.key] = r;
      if (!st.pick && r.years.length) {
        // "latest" is usually the newest numbered year again, so default to two real years.
        const num = r.years.map(a => a.year).filter(y => y !== "latest"), ys = num.length ? num : r.years.map(a => a.year);
        st.pick = { source: s.key, after: ys[ys.length - 1], before: ys.length > 1 ? ys[ys.length - 2] : null };
      }
      draw();
    }));
  }
  function setBox(bb) { st.bbox = bb; if (rect) map.removeLayer(rect); rect = L.rectangle(bboxBounds(bb), { color: COLORS().accent, weight: 2, fillOpacity: 0.1 }).addTo(map); load(); }
  function clickCell(source, year) {
    const p = st.pick;
    if (!p || p.source !== source) st.pick = { source, after: year, before: null };
    else if (year === p.after) st.pick = { ...p, before: null };
    else if (p.after !== "latest" && (year === "latest" || year > p.after)) st.pick = { ...p, before: p.after, after: year };
    else st.pick = { ...p, before: year };
    draw();
  }
  async function countS2() {
    st.s2 = "loading"; draw();
    st.s2 = await api(`/api/s2_passes?bbox=${st.bbox.join(",")}`).catch(e => ({ error: e.message, months: {}, years: [] }));
    draw();
  }
  async function run() {
    const p = st.pick; if (!p) return;
    const job = await guard(() => post("/api/jobs", { kind: "graves", name: I.name.value, bbox: st.bbox, case_id: I.caseSel.value || null,
      params: { source: p.source, after_year: p.after, before_year: p.before || "", mass: I.mass.checked, osm_file: I.osm.value.trim() } }));
    if (job) { pollJobs(); location.hash = `#/jobs/${job.id}`; }
  }
  function matrix() {
    const ready = st.sources.filter(s => st.years[s.key] && st.years[s.key] !== "loading");
    const cols = [...new Set(ready.flatMap(s => st.years[s.key].years.map(a => a.year)))].sort((a, b) => a === "latest" ? 1 : b === "latest" ? -1 : a - b);
    if (!cols.length) return h("p", { class: "muted" }, st.sources.some(s => st.years[s.key] === "loading") ? "Asking each service about this box…" : "No free high resolution imagery found for this box. Your own drone or aerial file still works in New search.");
    const grid = h("div", { class: "matrix", style: { gridTemplateColumns: `170px repeat(${cols.length}, minmax(0, 1fr))` } }, h("span", {}), cols.map(c => h("span", { class: "ch" }, c === "latest" ? "latest" : c)));
    st.sources.forEach(s => {
      const y = st.years[s.key];
      grid.append(h("span", { class: "rh" }, s.name.replace(/ \(.*\)$/, ""), h("div", { class: "small muted" }, `max ${s.max_km2} km² · ${s.license}`)));
      cols.forEach(c => {
        const a = y && y !== "loading" ? y.years.find(v => v.year === c) : null;
        if (!a) { grid.append(h("span", { class: "none" }, y === "loading" ? "…" : "–")); return; }
        const p = st.pick, cls = "cell" + (a.nir ? " nir" : "") + (p && p.source === s.key && p.after === c ? " search" : "") + (p && p.source === s.key && p.before === c ? " compare" : "");
        grid.append(h("button", { class: cls, type: "button", title: `${a.label}. Click to search this year, then click an earlier year to compare.`, onclick: () => clickCell(s.key, c) },
          `${Math.round(a.res_m * 100)}`, a.nir ? " IR" : ""));
      });
    });
    return h("div", { style: { overflowX: "auto" } }, grid);
  }
  function months() {
    if (!st.bbox) return null;
    if (!st.s2) return h("button", { class: "btn", type: "button", onclick: countS2 }, "Count Sentinel-2 passes for this box (takes about a minute)");
    if (st.s2 === "loading") return h("p", { class: "muted" }, "Asking Earth Search about four years of passes. This takes about a minute…");
    if (st.s2.error) return h("div", { class: "banner danger" }, st.s2.error);
    const shades = ["var(--line-2)", "#CFE8E6", "#9FD0CC", "#6DB8B2", "#3E9E97", "#0F6E6E"];
    const g = h("div", { class: "months" }, h("span", {}), "JFMAMJJASOND".split("").map(m => h("span", { class: "muted" }, m)));
    st.s2.years.forEach(y => {
      g.append(h("span", { style: { textAlign: "left", padding: "6px 0" } }, y));
      for (let m = 1; m <= 12; m++) {
        const k = `${y}-${String(m).padStart(2, "0")}`, c = st.s2.months[k];
        if (!c) { g.append(h("span", { class: "c muted" }, k > st.s2.through.slice(0, 7) ? "" : "0")); continue; }
        g.append(h("span", { class: "c", title: `${k}: ${c.clear} mostly clear of ${c.all} passes`, style: { background: shades[Math.min(5, c.clear)], color: c.clear >= 4 ? "#fff" : "var(--ink)" } }, c.clear));
      }
    });
    return h("div", {}, h("div", { style: { overflowX: "auto" } }, g),
      h("p", { class: "small muted" }, `Passes per month with granule cloud under ${st.s2.clear_below}%. 10 m pixels: for the wide-area scan and for timing, never for finding a grave.`));
  }
  function draw() {
    const p = st.pick, src = p ? st.sources.find(s => s.key === p.source) : null, a = st.bbox ? areaKm2(st.bbox) : 0;
    const yinfo = (key, year) => { const y = st.years[key]; return y && y !== "loading" ? y.years.find(v => v.year === year) : null; };
    const A = p ? yinfo(p.source, p.after) : null, B = p && p.before ? yinfo(p.source, p.before) : null;
    fill(panel, 
      h("div", { class: "field" }, h("span", { class: "label" }, "Area"), mapEl,
        h("div", { class: "row", style: { justifyContent: "space-between" } }, h("span", { class: "small muted" }, st.bbox ? `${fmtKm2(a)} · ${st.bbox[1].toFixed(4)}, ${st.bbox[0].toFixed(4)}` : "Draw a box on the map."),
          h("button", { class: "btn small", type: "button", onclick: () => drawer && drawer.start() }, icon("box"), st.bbox ? "Redraw" : "Draw box"))),
      h("section", { class: "card pad", "aria-labelledby": "plan-h" }, h("h2", { id: "plan-h" }, "Plan"),
        !p ? h("p", { class: "small muted", style: { margin: 0 } }, "Click a year in the table to search it. Click an earlier year of the same source to compare against it.") : [
          h("dl", { class: "facts" }, h("dt", {}, "Source"), h("dd", {}, src.name), h("dt", {}, "Search"), h("dd", {}, `${p.after}` + (A ? `, ${Math.round(A.res_m * 100)} cm${A.nir ? " with infrared" : ""}` : "")),
            h("dt", {}, "Compare"), h("dd", {}, p.before ? `${p.before}` + (B ? `, ${Math.round(B.res_m * 100)} cm${B.nir ? " with infrared" : ""}` : "") : "nothing, one image")),
          A && B && A.nir && B.nir ? h("div", { class: "banner info small" }, "Infrared in both years, so the plant vigour change layer is on.") : null,
          h("div", {}, h("div", { class: "row small muted", style: { justifyContent: "space-between" } }, h("span", {}, `Size against the ${src.name.split(" ")[0]} limit`), h("span", { class: "mono" }, `${a.toFixed(2)} / ${src.max_km2} km²`)),
            h("div", { class: "progress " + (a > src.max_km2 ? "meter-over" : "meter-ok"), style: { marginTop: "5px" } }, h("span", { style: { width: `${Math.min(100, a / src.max_km2 * 100)}%` } })))],
        h("div", { class: "field" }, h("label", { for: "im-case" }, "Case"), I.caseSel),
        h("div", { class: "field" }, h("label", { for: "im-name" }, "Name"), I.name),
        h("label", { class: "check" }, I.mass, h("span", {}, "Look for mass graves and trenches")),
        h("div", { class: "field" }, h("label", { for: "im-osm" }, "OpenStreetMap extract"), I.osm),
        h("button", { class: "btn primary big", type: "button", disabled: !p || (src && a > src.max_km2), onclick: run }, "Run grave search")));
    fill(body, 
      h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", {}, "Imagery"), h("p", { class: "sub" }, "What free high resolution imagery exists over a box, year by year. Each service is asked about this exact box. Numbers are pixel size in cm."))),
      h("section", { class: "card pad" }, h("h2", {}, "High resolution, by year"), st.bbox ? matrix() : h("p", { class: "muted" }, "Draw a box on the small map to start."),
        h("div", { class: "row small muted", style: { gap: "14px" } }, h("span", { class: "row", style: { gap: "6px" } }, h("span", { style: { width: "14px", height: "10px", outline: "2px solid var(--accent)" } }), "search year"),
          h("span", { class: "row", style: { gap: "6px" } }, h("span", { style: { width: "14px", height: "10px", outline: "2px dashed var(--ink)" } }), "compare year"), "IR: near infrared available")),
      h("section", { class: "card pad" }, h("h2", {}, "Sentinel-2, passes per month"), months() || h("p", { class: "muted" }, "Draw a box first.")));
  }
  draw();
  const page = h("div", { class: "workbench" }, panel, h("div", { class: "mapwrap", style: { minHeight: "auto" } }, body));
  return after(page, () => {
    map = baseMap(mapEl, { zoomLevel: 3, scale: false });
    drawer = boxDrawer(map, setBox);
    if (st.bbox) { setBox(st.bbox); map.fitBounds(bboxBounds(st.bbox).pad(0.6)); }
    setTimeout(() => map.invalidateSize(), 50);
    return null;
  });
}

// Settings ---------------------------------------------------------------------------

async function renderSettings() {
  const [set, cache, all] = await Promise.all([api("/api/settings"), api("/api/cache"), api("/api/sources_all")]);
  const draft = JSON.parse(JSON.stringify(set));
  const cacheBox = h("div");
  function drawCache(c) {
    const total = Object.values(c.groups).reduce((a, g) => a + g.bytes, 0);
    const colors = { areas: "var(--accent)", osm: "var(--warn)", linz: "var(--grave)" };
    fill(cacheBox, 
      h("div", { class: "row", style: { justifyContent: "space-between" } }, h("b", {}, `${fmtBytes(total)} used`), h("span", { class: "small muted" }, c.free_bytes !== null ? `${fmtBytes(c.free_bytes)} free on this disk` : "")),
      h("div", { class: "progress big stack" }, Object.entries(c.groups).map(([k, g]) => h("span", { style: { width: `${total ? g.bytes / total * 100 : 0}%`, background: colors[k] } }))),
      h("ul", { class: "list-plain small" }, Object.entries(c.groups).map(([k, g]) => h("li", { class: "row", style: { flexWrap: "nowrap" } },
        h("span", { class: "sw", style: { background: colors[k] } }), h("span", { style: { flex: 1 } }, g.label, k === "areas" && g.folders ? h("span", { class: "muted" }, `, ${g.folders} areas`) : null),
        h("span", { class: "mono" }, fmtBytes(g.bytes)),
        h("button", { class: "btn small", type: "button", disabled: !g.bytes, onclick: async () => {
          if (!confirm(`Delete the cached ${g.label.toLowerCase()} (${fmtBytes(g.bytes)})? Searches download it again when they need it.`)) return;
          const r = await guard(() => post("/api/cache/clear", { what: k })); if (r) { toast(`Cleared ${r.removed_folders} folder(s).`); drawCache(r); }
        } }, "Clear")))));
  }
  drawCache(cache);
  const tog = (key, title, desc) => h("label", { class: "check", style: { padding: "10px 0", borderTop: "1px solid var(--line-2)" } },
    h("input", { type: "checkbox", checked: !!draft[key], onchange: e => { draft[key] = e.target.checked; } }), h("span", {}, h("b", { style: { display: "block", fontWeight: 500 } }, title), h("span", { class: "small muted" }, desc)));
  const num = (key, label, step) => h("div", { class: "field" }, h("label", { for: `d-${key}` }, label),
    h("input", { class: "input", id: `d-${key}`, type: "number", step, value: String(draft.dumping[key]), oninput: e => { draft.dumping[key] = e.target.value; } }));
  async function save() {
    const r = await guard(() => post("/api/settings", draft));
    if (r) { S.settings = r; toast("Settings saved."); }
  }
  return h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", {}, "Settings"), h("p", { class: "sub" }, `Saved in ${set.output_dir}/settings.json on this computer.`)),
      h("button", { class: "btn primary", type: "button", onclick: save }, "Save settings")),
    h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(420px, 1fr))", gap: "16px", alignItems: "start" } },
      h("div", { style: { display: "flex", flexDirection: "column", gap: "16px" } },
        h("section", { class: "card pad", "aria-labelledby": "st-h" }, h("h2", { id: "st-h" }, "Storage"),
          h("div", { class: "field" }, h("span", { class: "label" }, "Download cache"), h("div", { class: "mono small" }, cache.path), h("span", { class: "small muted" }, "Set when starting: dgd ui --cache-dir FOLDER. Shared with the command line.")),
          cacheBox,
          h("div", { class: "field" }, h("span", { class: "label" }, "Results folder"), h("div", { class: "mono small" }, set.output_dir),
            h("span", { class: "small muted" }, "One folder per search, with its manifest and file hashes. Removing a search from a list never deletes its folder."))),
        h("section", { class: "card pad", "aria-labelledby": "src-h" }, h("div", { class: "card-head" }, h("h2", { id: "src-h" }, "Imagery sources for grave search"), h("span", { class: "small muted" }, "All free. Credit goes in every report.")),
          h("div", { class: "tblwrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Use", "Source", "Pixel", "Infrared", "Licence"].map(t => h("th", {}, t)))),
            h("tbody", {}, all.map(s => h("tr", {},
              h("td", {}, h("input", { type: "checkbox", "aria-label": `Use ${s.name}`, checked: !draft.sources_off.includes(s.key), style: { width: "18px", height: "18px", accentColor: "var(--accent)" },
                onchange: e => { draft.sources_off = e.target.checked ? draft.sources_off.filter(k => k !== s.key) : [...draft.sources_off, s.key]; } })),
              h("td", {}, s.name), h("td", {}, s.pixel), h("td", {}, s.nir), h("td", {}, s.license)))))))),
      h("div", { style: { display: "flex", flexDirection: "column", gap: "16px" } },
        h("section", { class: "card pad", "aria-labelledby": "nw-h" }, h("h2", { id: "nw-h" }, "Network and privacy"),
          h("p", { class: "small muted", style: { margin: 0 } }, "The server listens on 127.0.0.1 only, and every request needs this page's token. This is what leaves the computer."),
          tog("place_search", "Place search", "Sends what you type in Find a place to OpenStreetMap Nominatim. Coordinates you type are never sent."),
          tog("wayback", "Older imagery viewer", "Esri Wayback releases on the map, for looking only. Never used as input to a search."),
          tog("offline_map", "Offline map", "No map tiles, no place search, no older imagery: a blank map. Searches still download the imagery they need."),
          h("div", { style: { padding: "10px 0", borderTop: "1px solid var(--line-2)" } }, h("b", { style: { display: "block", fontWeight: 500 } }, "Searches always fetch"),
            h("span", { class: "small muted" }, "Sentinel-2 from Element84 Earth Search, land cover from ESA WorldCover, roads and buildings from Overpass, and the high resolution source you pick.")),
          h("div", { class: "field" }, h("label", { for: "ov" }, "Own Overpass server (optional)"),
            h("input", { class: "input mono", id: "ov", placeholder: "https://overpass.example.org/api/interpreter", value: draft.overpass_url, oninput: e => { draft.overpass_url = e.target.value; } }),
            h("span", { class: "small muted" }, "Tried before the public ones. Or skip the network and give grave searches a local .osm.pbf file."))),
        h("section", { class: "card pad", "aria-labelledby": "df-h" }, h("h2", { id: "df-h" }, "Search defaults"),
          h("div", { class: "grid2", style: { gridTemplateColumns: "repeat(3, minmax(0, 1fr))" } }, num("min_score", "Change score", "0.1"), num("min_area_m2", "Smallest lead, m²", "10"), num("min_event_days", "Lasts at least, days", "1")),
          h("p", { class: "small muted", style: { margin: 0 } }, `For dumping scans. Shipped values: score ${set.shipped_dumping.min_score}, ${set.shipped_dumping.min_area_m2} m², ${set.shipped_dumping.min_event_days} days. Burial scans keep their own preset.`),
          h("button", { class: "btn small", type: "button", onclick: () => { draft.dumping = { ...set.shipped_dumping }; ["min_score", "min_area_m2", "min_event_days"].forEach(k => { $(`#d-${k}`).value = draft.dumping[k]; }); } }, "Reset to the shipped values")),
        h("section", { class: "card pad", "aria-labelledby": "fs2-h" }, h("h2", { id: "fs2-h" }, "Field sheets"),
          h("div", { class: "field" }, h("label", { for: "fc" }, "Who to call, printed on every sheet"),
            h("input", { class: "input", id: "fc", placeholder: "e.g. forensic duty officer, +00 000 000 000", value: draft.field_contact, oninput: e => { draft.field_contact = e.target.value; } }))),
        h("section", { class: "card pad", "aria-labelledby": "ab-h" }, h("h2", { id: "ab-h" }, "About"),
          h("dl", { class: "facts" }, h("dt", {}, "Version"), h("dd", {}, window.DGD.version), h("dt", {}, "Server"), h("dd", { class: "mono" }, location.host),
            h("dt", {}, "Reports"), h("dd", {}, "Leads, not evidence. Every search writes a manifest with inputs, settings and file hashes."))))));
}

// Start ----------------------------------------------------------------------------

(async () => {
  try {
    S.info = await api("/api/info");
    S.settings = S.info.settings;
  } catch (e) {
    fill($("#main"), h("div", { class: "page" }, h("h1", {}, "Cannot reach the local server"), h("p", { class: "banner danger" }, e.message)));
    return;
  }
  await pollJobs();
  window.addEventListener("hashchange", route);
  route();
})();
