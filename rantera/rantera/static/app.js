"use strict";
/* RANTERA — tableau de bord de supervision. Aucune dépendance obligatoire (Leaflet optionnel pour les fonds de carte). */

const TYPE_LABEL = {collision_frontale:"Collision frontale",collision_laterale:"Collision latérale",collision_arriere:"Collision arrière",tonneau:"Tonneau",freinage_brutal:"Freinage brutal"};
const SEV_LABEL = {faible:"Faible",moyenne:"Moyenne",elevee:"Élevée"};
const SEV_COLOR = {faible:"#2e8b57",moyenne:"#d98200",elevee:"#c62828"};
const STATUS_LABEL = {nouveau:"Nouveau",en_cours:"En cours",traite:"Traité",faux_positif:"Faux positif"};
const POS_LABEL = {gps:"GPS",last_known:"Dernière position connue",unavailable:"Indisponible"};
const FEAT_LABEL = {pre_speed:"Vitesse avant choc (km/h)",delta_v:"Variation de vitesse ΔV (km/h)",peak_g:"Pic d'accélération (g)",peak_ax:"Pic longitudinal (g)",peak_ay:"Pic latéral (g)",peak_az:"Pic vertical (g)",impact_ms:"Durée du choc (ms)",roll_max:"Roulis max (°)",post_speed:"Vitesse après choc (km/h)",yaw_rate_peak:"Vitesse de lacet max (°/s)",impact_angle:"Direction du choc (°)",jerk_max:"À-coup max (g/s)",type_code:"Code de type",speed_drop:"Perte de vitesse (km/h)",peak_decel_g:"Décélération max (g)",duration_s:"Durée (s)"};

const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtDate = iso => iso ? new Date(iso).toLocaleString("fr-FR", {dateStyle:"short", timeStyle:"medium", timeZone:"UTC"}) : "—";
const fmtCoord = (a, b) => a == null ? "—" : `${(+a).toFixed(5)}, ${(+b).toFixed(5)}`;

const S = {
  key: sessionStorage.getItem("rantera_key") || "",
  view: "live", feed: [], known: null, selected: null, vehicles: [], stats: null,
  page: 0, pageSize: 20, filters: {}, online: false,
};

/* ---------- API ---------- */
async function api(path, opts = {}) {
  const res = await fetch(path, {...opts, headers: {"X-API-Key": S.key, "Content-Type": "application/json", ...(opts.headers||{})}});
  if (res.status === 401) { setOnline(false, "Clé API requise"); openKeyDialog(); throw new Error("Clé API invalide"); }
  if (!res.ok) { let d = res.statusText; try { d = (await res.json()).detail || d; } catch {} throw new Error(d); }
  return res;
}
const getJSON = async p => (await api(p)).json();
function setOnline(ok, text) {
  S.online = ok;
  const el = $("#conn"); el.textContent = text || (ok ? "Connecté" : "Hors ligne");
  el.className = "pill " + (ok ? "ok" : "bad");
}
function toast(msg, err=false) {
  const t = document.createElement("div"); t.className = "toast" + (err ? " err" : ""); t.textContent = msg;
  $("#toasts").append(t); setTimeout(() => t.remove(), 5000);
}
async function download(path, name) {
  try {
    const blob = await (await api(path)).blob();
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) { toast("Export impossible : " + e.message, true); }
}
const qs = o => Object.entries(o).filter(([,v]) => v !== "" && v != null).map(([k,v]) => `${k}=${encodeURIComponent(v)}`).join("&");

/* ---------- Carte (Leaflet si disponible, sinon canvas) ---------- */
const MapView = {
  kind: null, L: null, map: null, layer: null, canvas: null, pts: [],
  async init() {
    const host = $("#map");
    try {
      await new Promise((ok, ko) => {
        const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"; document.head.append(css);
        const s = document.createElement("script"); s.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"; s.onload = ok; s.onerror = ko; document.head.append(s);
        setTimeout(ko, 6000);
      });
      if (!window.L) throw new Error("no leaflet");
      this.L = window.L; this.kind = "leaflet";
      this.map = L.map(host).setView([36.8065, 10.1815], 13);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 19, attribution: "© OpenStreetMap"}).addTo(this.map);
      this.layer = L.layerGroup().addTo(this.map);
      $("#map-note").textContent = "Carte OpenStreetMap";
    } catch {
      this.kind = "canvas"; this.canvas = document.createElement("canvas"); host.append(this.canvas);
      this.canvas.addEventListener("click", e => this.canvasClick(e));
      $("#map-note").textContent = "Carte hors ligne (fond OpenStreetMap indisponible)";
      new ResizeObserver(() => this.draw()).observe(host);
    }
  },
  update(incidents, vehicles) {
    this.pts = [];
    incidents.filter(i => i.lat != null).forEach(i => this.pts.push({k:"i", id:i.incident_id, lat:i.lat, lon:i.lon, color:i.kind==="accident"?SEV_COLOR[i.severity]:"#7a8791", sel:i.incident_id===S.selected, anom:i.kind!=="accident", label:`${TYPE_LABEL[i.type]||i.type} — ${SEV_LABEL[i.severity]}`}));
    vehicles.filter(v => v.last_lat != null).forEach(v => this.pts.push({k:"v", id:v.vehicle_id, lat:v.last_lat, lon:v.last_lon, color:"#0b5f8f", active:v.active, label:`${v.vehicle_id} — ${Math.round(v.last_speed||0)} km/h`}));
    if (this.kind === "leaflet") this.drawLeaflet(); else if (this.kind === "canvas") this.draw();
  },
  drawLeaflet() {
    this.layer.clearLayers();
    this.pts.forEach(p => {
      const m = L.circleMarker([p.lat, p.lon], p.k === "v"
        ? {radius: 6, color: "#fff", weight: 2, fillColor: p.color, fillOpacity: p.active ? 1 : .4}
        : {radius: p.sel ? 13 : 9, color: p.sel ? "#16222d" : "#fff", weight: p.sel ? 3 : 2, fillColor: p.color, fillOpacity: .95});
      m.bindTooltip(esc(p.label)); if (p.k === "i") m.on("click", () => selectIncident(p.id)); m.addTo(this.layer);
    });
    if (!this._fitted && this.pts.length) { this._fitted = true; this.map.fitBounds(this.pts.map(p => [p.lat, p.lon]), {padding:[40,40], maxZoom: 16}); }
    if (this.focus) { this.map.setView(this.focus, Math.max(this.map.getZoom(), 15)); this.focus = null; }
  },
  proj() {
    const c = this.canvas, w = c.clientWidth, h = c.clientHeight;
    const pts = this.pts.length ? this.pts : [{lat:36.8065,lon:10.1815}];
    let la0 = Math.min(...pts.map(p=>p.lat)), la1 = Math.max(...pts.map(p=>p.lat)), lo0 = Math.min(...pts.map(p=>p.lon)), lo1 = Math.max(...pts.map(p=>p.lon));
    const pad = Math.max(.002, (la1-la0)*.15, (lo1-lo0)*.15); la0-=pad; la1+=pad; lo0-=pad; lo1+=pad;
    const kx = Math.cos((la0+la1)/2*Math.PI/180), sx = (w-40)/((lo1-lo0)*kx), sy = (h-40)/(la1-la0), s = Math.min(sx, sy);
    const ox = (w - (lo1-lo0)*kx*s)/2, oy = (h - (la1-la0)*s)/2;
    return (lat, lon) => [ox + (lon-lo0)*kx*s, h - oy - (lat-la0)*s];
  },
  draw() {
    const c = this.canvas; if (!c) return;
    const dpr = window.devicePixelRatio || 1; c.width = c.clientWidth*dpr; c.height = c.clientHeight*dpr;
    const g = c.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0,0,c.clientWidth,c.clientHeight);
    g.strokeStyle = "#d3dadf"; g.lineWidth = 1;
    for (let x = 0; x < c.clientWidth; x += 60) { g.beginPath(); g.moveTo(x,0); g.lineTo(x,c.clientHeight); g.stroke(); }
    for (let y = 0; y < c.clientHeight; y += 60) { g.beginPath(); g.moveTo(0,y); g.lineTo(c.clientWidth,y); g.stroke(); }
    const P = this.proj(); this.hit = [];
    [...this.pts].sort((a,b) => (a.k==="v"?0:1)-(b.k==="v"?0:1)).forEach(p => {
      const [x,y] = P(p.lat, p.lon), r = p.k==="v" ? 6 : (p.sel ? 13 : 9);
      g.globalAlpha = p.k==="v" && !p.active ? .4 : 1; g.fillStyle = p.color; g.strokeStyle = p.sel ? "#16222d" : "#fff"; g.lineWidth = p.sel ? 3 : 2;
      g.beginPath(); g.arc(x,y,r,0,7); g.fill(); g.stroke(); g.globalAlpha = 1; this.hit.push({x,y,r:r+4,p});
    });
    g.fillStyle = "#6b7a86"; g.font = "12px sans-serif"; g.fillText("N ↑", 12, 20);
  },
  canvasClick(e) {
    const r = this.canvas.getBoundingClientRect(), x = e.clientX-r.left, y = e.clientY-r.top;
    const h = (this.hit||[]).filter(h => h.p.k==="i" && Math.hypot(h.x-x,h.y-y) <= h.r).pop();
    if (h) selectIncident(h.p.id);
  },
  focusOn(lat, lon) { if (this.kind === "leaflet") { this.focus = [lat, lon]; this.drawLeaflet(); } },
};

/* ---------- Rendu : supervision ---------- */
function sevTag(i) { return i.kind === "accident" ? `<span class="tag sev sev-${i.severity}">${SEV_LABEL[i.severity]}</span>` : `<span class="tag anom">Anomalie</span>`; }
function srcTag(s) { return s === "simulated" ? `<span class="tag sim">Simulé</span>` : `<span class="tag real">Réel</span>`; }
function ago(iso) { const s = (Date.now() - new Date(iso)) / 1000; return s < 60 ? "à l'instant" : s < 3600 ? `il y a ${Math.round(s/60)} min` : s < 86400 ? `il y a ${Math.round(s/3600)} h` : fmtDate(iso); }

function renderKpis() {
  const st = S.stats; if (!st) return;
  const active = S.vehicles.filter(v => v.active).length, ob = st.outbox || {};
  const k = [
    ["", st.total, "incidents enregistrés"],
    ["elevee", st.by_severity.elevee, "accidents de gravité élevée"],
    ["warn", st.open, "à traiter (nouveau, en cours)"],
    [(ob.pending||0)+(ob.failed||0) ? "warn" : "ok", (ob.pending||0)+(ob.failed||0), "alertes en attente d'envoi"],
    [active ? "ok" : "", active, "véhicules actifs"],
  ];
  $("#kpis").innerHTML = k.map(([c,n,l]) => `<div class="kpi ${c}"><b>${n}</b><span>${l}</span></div>`).join("");
}
function renderBanner() {
  const urgent = S.feed.filter(i => i.kind==="accident" && i.severity==="elevee" && i.status==="nouveau");
  const b = $("#banner");
  if (urgent.length) { b.hidden = false; b.textContent = `${urgent.length} accident(s) de gravité élevée non pris en charge — ${TYPE_LABEL[urgent[0].type]}, ${ago(urgent[0].occurred_at)}`; } else b.hidden = true;
}
function renderFeed(fresh = new Set()) {
  const ul = $("#feed");
  if (!S.feed.length) { ul.innerHTML = `<li class="empty" style="display:block;cursor:default">Aucun incident. Lancez une simulation avec « Simuler un accident ».</li>`; return; }
  ul.innerHTML = S.feed.slice(0, 40).map(i => `<li data-id="${esc(i.incident_id)}" class="${i.incident_id===S.selected?"sel":""} ${fresh.has(i.incident_id)?"fresh":""}">
    <span class="bar ${i.kind==="accident"?"sev-"+i.severity:""}" style="${i.kind!=="accident"?"background:#9aa7b0":""}"></span>
    <div><div class="t">${esc(TYPE_LABEL[i.type]||i.type)}</div><div class="m">${esc(i.vehicle_id)} · ${ago(i.occurred_at)} · ${STATUS_LABEL[i.status]}</div></div>
    <div style="text-align:right">${sevTag(i)}<br>${srcTag(i.source)}</div></li>`).join("");
  $$("#feed li[data-id]").forEach(li => li.onclick = () => selectIncident(li.dataset.id));
}

async function refreshLive() {
  try {
    const [inc, veh, st] = await Promise.all([getJSON("/api/incidents?limit=200"), getJSON("/api/vehicles"), getJSON("/api/stats")]);
    setOnline(true);
    const fresh = new Set();
    if (S.known === null) S.known = new Set(inc.items.map(i => i.incident_id));
    else inc.items.forEach(i => { if (!S.known.has(i.incident_id)) { S.known.add(i.incident_id); fresh.add(i.incident_id);
      toast(`${i.kind==="accident" ? "Alerte" : "Événement"} : ${TYPE_LABEL[i.type]||i.type}${i.kind==="accident" ? " — gravité " + SEV_LABEL[i.severity].toLowerCase() : ""}`); } });
    S.feed = inc.items; S.vehicles = veh.items; S.stats = st;
    renderKpis(); renderBanner(); renderFeed(fresh); MapView.update(S.feed, S.vehicles);
    if (S.view === "history") loadHistory(); if (S.view === "analysis") renderAnalysis(); if (S.view === "alerts") loadAlerts();
    if (S.selected && !$("#detail").hidden && !document.activeElement.closest?.("#detail")) openDetail(S.selected, true);
  } catch (e) { if (e.message !== "Clé API invalide") setOnline(false, "Hors ligne"); }
}

/* ---------- Détail ---------- */
async function selectIncident(id) { S.selected = id; renderFeed(); MapView.update(S.feed, S.vehicles); await openDetail(id); }
async function openDetail(id, quiet = false) {
  let d; try { d = await getJSON("/api/incidents/" + encodeURIComponent(id)); } catch (e) { if (!quiet) toast(e.message, true); return; }
  const el = $("#detail"); el.hidden = false;
  const real = d.source === "real";
  const feats = Object.entries(d.features).map(([k,v]) => `<span>${esc(FEAT_LABEL[k]||k)}</span><span>${esc(v)}</span>`).join("");
  el.innerHTML = `
  <div class="head"><div><h3>${esc(TYPE_LABEL[d.type]||d.type)}</h3><div class="hint">${esc(d.incident_id)} · ${esc(d.vehicle_id)}</div></div>
    <button class="btn ghost small" id="d-close" aria-label="Fermer">Fermer</button></div>
  <div class="row">${sevTag(d)} ${srcTag(d.source)} <span class="tag">${STATUS_LABEL[d.status]}</span> ${d.validated ? '<span class="tag real">Validé</span>' : ""}</div>
  <dl><dt>Survenu le</dt><dd>${fmtDate(d.occurred_at)} UTC</dd>
      <dt>Position</dt><dd>${fmtCoord(d.lat, d.lon)}<br><span class="hint">Source : ${POS_LABEL[d.position_source]}</span></dd>
      ${d.kind==="accident" ? `<dt>Gravité (règles)</dt><dd>${SEV_LABEL[d.severity_rules]||"—"}</dd>
      <dt>Gravité (IA)</dt><dd>${d.severity_ml ? SEV_LABEL[d.severity_ml] + ` (confiance ${Math.round(d.ml_confidence*100)} %)` : "non disponible"}</dd>
      <dt>Moteur</dt><dd>${esc(d.engine)}</dd>` : ""}
      ${d.ground_truth ? `<dt>Scénario simulé</dt><dd>${esc(d.ground_truth)}</dd>` : ""}</dl>
  ${d.source==="simulated" ? `<p class="hint">Incident issu d'une simulation : il ne constitue pas une détection validée sur données réelles.</p>` : ""}
  <div class="sec"><h2>Prise en charge</h2>
    <div class="row"><select id="d-status">${Object.entries(STATUS_LABEL).map(([k,v]) => `<option value="${k}" ${k===d.status?"selected":""}>${v}</option>`).join("")}</select>
    <input type="text" id="d-note" placeholder="Note (facultatif)" maxlength="500" style="flex:1;min-width:140px">
    <button class="btn small" id="d-save">Enregistrer</button></div>
    <label class="row"><input type="checkbox" id="d-valid" ${d.validated?"checked":""} ${real?"":"disabled"}> Détection confirmée par un opérateur ${real?"":"(réservé aux incidents réels)"}</label>
    ${d.notes ? `<div class="note">${esc(d.notes)}</div>` : ""}</div>
  <div class="sec"><h2>Trajectoire</h2><canvas id="d-track" width="420" height="150"></canvas></div>
  ${d.alerts.length ? `<div class="sec"><h2>Alerte</h2>${d.alerts.map(a => `<div class="row"><span class="tag">${esc(a.status)}</span><span class="hint">${esc(a.alert_id)} · ${a.attempts} tentative(s)${a.last_error ? " · " + esc(a.last_error) : ""}</span></div>`).join("")}</div>` : ""}
  <div class="sec"><h2>Mesures analysées</h2><div class="feat">${feats}</div></div>
  <div class="sec"><h2>Historique de traitement</h2>${d.history.map(h => `<div class="hint">${fmtDate(h.changed_at)} — ${h.old_status ? STATUS_LABEL[h.old_status] + " → " : ""}${STATUS_LABEL[h.new_status]}${h.note ? " · " + esc(h.note) : ""}</div>`).join("")}</div>`;
  $("#d-close").onclick = () => { el.hidden = true; S.selected = null; renderFeed(); MapView.update(S.feed, S.vehicles); };
  $("#d-save").onclick = async () => {
    try { await api("/api/incidents/" + encodeURIComponent(id), {method:"PATCH", body: JSON.stringify({status: $("#d-status").value, note: $("#d-note").value || undefined})}); toast("Incident mis à jour"); await refreshLive(); await openDetail(id); }
    catch (e) { toast(e.message, true); }
  };
  $("#d-valid").onchange = async ev => { try { await api("/api/incidents/" + encodeURIComponent(id), {method:"PATCH", body: JSON.stringify({validated: ev.target.checked})}); toast("Validation enregistrée"); await openDetail(id); } catch (e) { toast(e.message, true); } };
  drawTrack($("#d-track"), d);
  if (!quiet && d.lat != null) MapView.focusOn(d.lat, d.lon);
}
function drawTrack(c, d) {
  const g = c.getContext("2d"), W = c.width, H = c.height; g.clearRect(0,0,W,H);
  const pts = d.track.map(p => [p.lat, p.lon]); if (d.lat != null) pts.push([d.lat, d.lon]);
  if (pts.length < 2) { g.fillStyle = "#6b7a86"; g.font = "13px sans-serif"; g.fillText(d.lat == null ? "Aucune position disponible" : "Trajectoire non enregistrée", 12, 24); if (pts.length===1) { g.fillStyle = SEV_COLOR[d.severity]; g.beginPath(); g.arc(W/2,H/2,7,0,7); g.fill(); } return; }
  const la = pts.map(p=>p[0]), lo = pts.map(p=>p[1]); const la0=Math.min(...la), la1=Math.max(...la), lo0=Math.min(...lo), lo1=Math.max(...lo);
  const kx = Math.cos((la0+la1)/2*Math.PI/180), s = Math.min((W-30)/(((lo1-lo0)*kx)||1e-9), (H-30)/((la1-la0)||1e-9));
  const P = (a,b) => [15 + (b-lo0)*kx*s, H - 15 - (a-la0)*s];
  g.strokeStyle = "#0b5f8f"; g.lineWidth = 2; g.beginPath(); d.track.forEach((p,i) => { const [x,y] = P(p.lat,p.lon); i ? g.lineTo(x,y) : g.moveTo(x,y); }); g.stroke();
  if (d.lat != null) { const [x,y] = P(d.lat,d.lon); g.fillStyle = SEV_COLOR[d.severity] || "#c62828"; g.strokeStyle = "#fff"; g.lineWidth = 2; g.beginPath(); g.arc(x,y,7,0,7); g.fill(); g.stroke(); }
}

/* ---------- Historique ---------- */
async function loadHistory() {
  const q = qs({...S.filters, limit: S.pageSize, offset: S.page * S.pageSize});
  try {
    const d = await getJSON("/api/incidents?" + q);
    const tb = $("#hist-table tbody");
    tb.innerHTML = d.items.length ? d.items.map(i => `<tr class="click" data-id="${esc(i.incident_id)}"><td>${fmtDate(i.occurred_at)}</td><td>${esc(i.vehicle_id)}</td><td>${esc(TYPE_LABEL[i.type]||i.type)}</td><td>${sevTag(i)}</td><td>${STATUS_LABEL[i.status]}</td><td>${srcTag(i.source)}</td><td>${i.lat==null?"—":fmtCoord(i.lat,i.lon)}</td></tr>`).join("")
      : `<tr><td colspan="7" class="empty">Aucun incident ne correspond à ces filtres.</td></tr>`;
    $$("#hist-table tr.click").forEach(tr => tr.onclick = () => selectIncident(tr.dataset.id));
    const pages = Math.max(1, Math.ceil(d.total / S.pageSize));
    $("#pg-info").textContent = `Page ${S.page+1} / ${pages} — ${d.total} incident(s)`;
    $("#pg-prev").disabled = S.page === 0; $("#pg-next").disabled = S.page + 1 >= pages;
  } catch (e) { toast(e.message, true); }
}

/* ---------- Analyse ---------- */
function bars(canvas, labels, values, colors) {
  const g = canvas.getContext("2d"), W = canvas.width, H = canvas.height; g.clearRect(0,0,W,H);
  const max = Math.max(1, ...values), pl = 34, pb = 38, pt = 12, bw = (W-pl-10) / Math.max(1, labels.length);
  g.font = "12px sans-serif"; g.fillStyle = "#6b7a86"; g.strokeStyle = "#e1e6ea";
  for (let i = 0; i <= 4; i++) { const y = pt + (H-pb-pt) * (1 - i/4); g.beginPath(); g.moveTo(pl,y); g.lineTo(W-6,y); g.stroke(); g.textAlign = "right"; g.fillText(Math.round(max*i/4), pl-6, y+4); }
  labels.forEach((l, i) => {
    const h = (H-pb-pt) * values[i]/max, x = pl + i*bw + bw*.15;
    g.fillStyle = Array.isArray(colors) ? colors[i % colors.length] : colors; g.fillRect(x, H-pb-h, bw*.7, h);
    g.fillStyle = "#16222d"; g.textAlign = "center"; if (values[i]) g.fillText(values[i], x+bw*.35, H-pb-h-4);
    g.fillStyle = "#6b7a86"; const t = String(l); g.fillText(t.length > 12 ? t.slice(0,11)+"…" : t, x+bw*.35, H-pb+16);
  });
  if (!values.some(v => v)) { g.fillStyle = "#6b7a86"; g.textAlign = "center"; g.fillText("Aucune donnée", W/2, H/2); }
}
async function renderAnalysis() {
  try {
    const st = await getJSON("/api/stats?" + qs(S.filters)); S.stats = st;
    bars($("#ch-sev"), Object.keys(st.by_severity).map(k => SEV_LABEL[k]), Object.values(st.by_severity), Object.keys(st.by_severity).map(k => SEV_COLOR[k]));
    bars($("#ch-type"), Object.keys(st.by_type).map(k => TYPE_LABEL[k]||k), Object.values(st.by_type), "#0b5f8f");
    bars($("#ch-day"), Object.keys(st.by_day).slice(-10).map(d => d.slice(5)), Object.values(st.by_day).slice(-10), "#0b5f8f");
    bars($("#ch-hour"), Object.keys(st.by_hour), Object.values(st.by_hour), "#3d4c59");
    $("#hotspots").innerHTML = st.hotspots.length ? st.hotspots.map(h => `<li>${fmtCoord(h.lat,h.lon)} — ${h.count} accident(s), score ${h.score}</li>`).join("") : `<li class="hint">Aucun accident géolocalisé pour l'instant.</li>`;
    const m = await getJSON("/api/model");
    $("#model-info").innerHTML = m.loaded ? `Moteur : <b>${esc(m.engine)}</b>. Exactitude mesurée sur scénarios <b>simulés</b> : ${Math.round((m.metrics.accuracy||0)*1000)/10} %. Ces chiffres ne prouvent pas la fiabilité sur accidents réels.` : "Modèle IA non chargé : estimation par règles uniquement.";
  } catch (e) { toast(e.message, true); }
}

/* ---------- Alertes ---------- */
async function loadAlerts() {
  try {
    const d = await getJSON("/api/alerts?limit=200"); const tb = $("#alert-table tbody");
    tb.innerHTML = d.items.length ? d.items.map(a => `<tr><td>${esc(a.alert_id)}</td><td>${fmtDate(a.created_at)}</td><td>${esc(TYPE_LABEL[a.payload.type]||a.payload.type)}</td><td><span class="tag sev sev-${a.payload.severity}">${SEV_LABEL[a.payload.severity]}</span></td>
      <td><span class="tag">${a.status === "sent" ? "Envoyée (" + esc(a.channel) + ")" : a.status === "pending" ? "En attente" : "Échec"}</span></td><td>${a.attempts}</td><td>${esc(a.last_error||"")}</td>
      <td>${a.status !== "sent" ? `<button class="btn ghost small" data-retry="${esc(a.alert_id)}">Renvoyer</button>` : ""}</td></tr>`).join("") : `<tr><td colspan="8" class="empty">Aucune alerte.</td></tr>`;
    $$("[data-retry]").forEach(b => b.onclick = async () => { try { await api(`/api/alerts/${b.dataset.retry}/retry`, {method:"POST"}); toast("Nouvel envoi tenté"); loadAlerts(); } catch (e) { toast(e.message, true); } });
  } catch (e) { toast(e.message, true); }
}

/* ---------- Navigation & dialogues ---------- */
function setView(v) {
  S.view = v; $$("#nav button").forEach(b => b.classList.toggle("on", b.dataset.view === v));
  $$(".view").forEach(s => s.hidden = s.id !== "view-" + v);
  if (v === "history") loadHistory(); if (v === "analysis") renderAnalysis(); if (v === "alerts") loadAlerts();
  if (v === "live") setTimeout(() => MapView.map && MapView.map.invalidateSize(), 50);
}
function openKeyDialog() { const d = $("#dlg-key"); if (!d.open) { $("#in-key").value = ""; d.showModal(); } }

$$("#nav button").forEach(b => b.onclick = () => setView(b.dataset.view));
$("#btn-key").onclick = openKeyDialog;
$("#dlg-key").addEventListener("close", () => { const v = $("#in-key").value.trim(); if (v) { S.key = v; sessionStorage.setItem("rantera_key", v); S.known = null; refreshLive(); } });
$("#btn-sim").onclick = () => $("#dlg-sim").showModal();
$("#dlg-sim").addEventListener("close", async ev => {
  if (ev.target.returnValue !== "go") return;
  const sc = $("#sim-scenario").value;
  try {
    const r = await (await api("/api/simulate", {method:"POST", body: JSON.stringify({scenario: sc, gps_loss: $("#sim-gps").checked})})).json();
    toast(r.incidents.length ? `Simulation : ${r.incidents.map(i => TYPE_LABEL[i.type] + (i.kind==="accident" ? " (" + SEV_LABEL[i.severity].toLowerCase() + ")" : "")).join(", ")}` : (r.message || "Aucun incident détecté"));
    await refreshLive();
  } catch (e) { toast("Simulation impossible : " + e.message, true); }
});
$("#filters").addEventListener("submit", e => { e.preventDefault(); S.filters = Object.fromEntries(new FormData(e.target)); S.page = 0; loadHistory(); });
$("#f-reset").onclick = () => setTimeout(() => { S.filters = {}; S.page = 0; loadHistory(); }, 0);
$("#pg-prev").onclick = () => { S.page = Math.max(0, S.page - 1); loadHistory(); };
$("#pg-next").onclick = () => { S.page++; loadHistory(); };
$("#btn-export").onclick = () => download("/api/export/incidents.csv?" + qs(S.filters), "incidents.csv");
$("#btn-export-feat").onclick = () => download("/api/export/features.csv", "features.csv");
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#detail").hidden) $("#d-close")?.click(); });

(async function main() {
  await MapView.init();
  if (!S.key) openKeyDialog();
  await refreshLive();
  setInterval(refreshLive, 2500);
})();
