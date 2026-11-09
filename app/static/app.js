"use strict";
const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Errore ${res.status}`);
  }
  return res.json();
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

// ---- formati --------------------------------------------------------------------------------
function fmtTime(sec) {
  if (sec == null) return "–";
  const s = Math.round(sec), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}
const fmtPace = sec => sec ? `${fmtTime(sec)}/km` : "–";
const fmtKm = m => m ? (m / 1000).toFixed(2) : "–";
const num = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("it-IT", { maximumFractionDigits: d, minimumFractionDigits: d });
const fmtDate = s => s ? new Date(s.slice(0, 10) + "T12:00").toLocaleDateString("it-IT", { day: "numeric", month: "short", year: "numeric" }) : "–";
const shortDate = s => new Date(s.slice(0, 10) + "T12:00").toLocaleDateString("it-IT", { day: "numeric", month: "short" });
const monthLabel = s => new Date(s + "-15T12:00").toLocaleDateString("it-IT", { month: "short", year: "2-digit" });
const paceOf = a => (a.moving_s || a.duration_s) && a.distance_m ? (a.moving_s || a.duration_s) / a.distance_m * 1000 : null;
const TYPE_NAMES = { running: "Corsa", treadmill_running: "Tapis roulant", trail_running: "Trail", track_running: "Pista", indoor_running: "Corsa indoor", strength_training: "Forza", cycling: "Bici", road_biking: "Bici da strada", mountain_biking: "MTB", lap_swimming: "Nuoto", walking: "Camminata", hiking: "Escursione", other: "Altro" };
const typeName = t => TYPE_NAMES[t] || String(t || "").replace(/_/g, " ");

// ---- grafici --------------------------------------------------------------------------------
let charts = [];
const chartData = new Map();
function destroyCharts() { charts.forEach(c => c.destroy()); charts = []; chartData.clear(); }

function card(id, title, subtitle = "", span = "s12", short = false) {
  return `<section class="card ${span}"><div class="card-head"><div><h2>${esc(title)}</h2>${subtitle ? `<p class="muted small">${esc(subtitle)}</p>` : ""}</div>
    <button class="linkish" data-table="${id}">Tabella</button></div>
    <div id="${id}-legend"></div><div class="chart ${short ? "short" : ""}" id="${id}-box"><canvas id="${id}" role="img" aria-label="${esc(title)}"></canvas></div>
    <div class="table-wrap" id="${id}-table" hidden></div></section>`;
}

function drawChart(id, type, labels, sets, opts = {}) {
  const el = document.getElementById(id);
  if (!el) return;
  const fmt = opts.format || (v => num(v, opts.decimals ?? 1));
  const line = type === "line";
  const datasets = sets.map(s => ({
    label: s.label, data: s.data, stack: opts.stacked ? "a" : undefined,
    backgroundColor: s.color, borderColor: s.color,
    borderWidth: line ? 2 : 0, borderRadius: line ? 0 : (opts.stacked ? 0 : 4), borderSkipped: "bottom",
    pointRadius: s.points ? 3 : 0, pointHoverRadius: 5, pointHitRadius: 12, tension: s.step ? 0 : .25, stepped: s.step ? "after" : false,
    spanGaps: true, maxBarThickness: 26, barPercentage: .78, categoryPercentage: .92,
  }));
  if (sets.length > 1) {
    $(`#${id}-legend`).innerHTML = `<div class="legend">${sets.map(s => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("")}</div>`;
  }
  const ink = cssVar("--ink-2"), grid = cssVar("--grid");
  charts.push(new Chart(el, {
    type, data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, tooltip: {
        backgroundColor: cssVar("--surface"), titleColor: cssVar("--ink"), bodyColor: cssVar("--ink"), borderColor: cssVar("--line"), borderWidth: 1,
        padding: 10, boxWidth: 8, boxHeight: 8, usePointStyle: true,
        callbacks: { label: c => ` ${c.dataset.label}: ${fmt(c.parsed.y)}${opts.unit ? " " + opts.unit : ""}` } } },
      scales: {
        x: { stacked: !!opts.stacked, grid: { display: false }, border: { color: grid }, ticks: { color: ink, maxTicksLimit: 8, maxRotation: 0, autoSkipPadding: 14 } },
        y: { stacked: !!opts.stacked, reverse: !!opts.reverse, beginAtZero: opts.zero ?? type === "bar", grid: { color: grid }, border: { display: false }, ticks: { color: ink, maxTicksLimit: 5, callback: v => fmt(v) } },
      },
    },
  }));
  chartData.set(id, { labels, sets, fmt });
}

document.addEventListener("click", e => {
  const btn = e.target.closest("[data-table]");
  if (!btn) return;
  const id = btn.dataset.table, data = chartData.get(id), box = $(`#${id}-box`), table = $(`#${id}-table`);
  if (!data) return;
  const showing = table.hidden;
  table.hidden = !showing; box.hidden = showing; btn.textContent = showing ? "Grafico" : "Tabella";
  if (showing) {
    table.innerHTML = `<table><thead><tr><th>Periodo</th>${data.sets.map(s => `<th class="r">${esc(s.label)}</th>`).join("")}</tr></thead>
      <tbody>${data.labels.map((l, i) => `<tr><td>${esc(l)}</td>${data.sets.map(s => `<td class="r num">${s.data[i] == null ? "–" : esc(data.fmt(s.data[i]))}</td>`).join("")}</tr>`).reverse().join("")}</tbody></table>`;
  }
});

// ---- componenti -----------------------------------------------------------------------------
const tile = (label, value, unit, note) => `<div class="tile"><div class="label">${esc(label)}</div><div class="value">${value}${unit ? ` <small>${esc(unit)}</small>` : ""}</div>${note ? `<div class="muted small">${esc(note)}</div>` : ""}</div>`;
const REC_TAGS = { warn: "▲ Attenzione", info: "● Suggerimento", good: "✓ Va bene" };
const recCards = items => items.length ? `<div class="recs">${items.map(r => `<article class="rec ${r.level}"><span class="tag">${REC_TAGS[r.level]}</span>
  <strong>${esc(r.title)}</strong><p class="muted small">${esc(r.detail)}</p><p class="small action">${esc(r.action)}</p></article>`).join("")}</div>` : `<p class="muted">Servono più dati per generare consigli.</p>`;
const empty = text => `<div class="card empty">${text}</div>`;
const noData = () => empty(`Nessuna attività ancora. Vai in <a href="#impostazioni">Impostazioni</a>, aggiungi la chiave Garmin e sincronizza.`);

function activityRows(items) {
  return items.map(a => `<tr class="click" data-activity="${esc(a.id)}" tabindex="0">
    <td>${fmtDate(a.start_local)}</td><td class="wrap">${esc(a.name || typeName(a.type))}</td><td>${esc(typeName(a.type))}</td>
    <td class="r num">${fmtKm(a.distance_m)}</td><td class="r num">${fmtTime(a.moving_s || a.duration_s)}</td>
    <td class="r num">${a.type.includes("run") ? fmtPace(paceOf(a)) : "–"}</td><td class="r num">${num(a.avg_hr)}</td></tr>`).join("");
}
const activityHead = `<thead><tr><th>Data</th><th>Nome</th><th>Tipo</th><th class="r">Km</th><th class="r">Tempo</th><th class="r">Ritmo</th><th class="r">FC media</th></tr></thead>`;

function openFromRow(e) {
  const row = e.target.closest("[data-activity]");
  if (row && (e.type === "click" || e.key === "Enter")) openActivity(row.dataset.activity);
}
document.addEventListener("click", openFromRow);
document.addEventListener("keydown", openFromRow);

async function openActivity(id) {
  const a = await api(`/api/activities/${encodeURIComponent(id)}`);
  const drawer = $("#drawer");
  const run = a.type.includes("run");
  drawer.innerHTML = `<div class="panel"><div class="head" style="margin:0"><div><h1>${esc(a.name || typeName(a.type))}</h1><p class="muted">${fmtDate(a.start_local)} · ${esc(typeName(a.type))}</p></div><button class="btn" id="closeDrawer">Chiudi</button></div>
    <div class="tiles">${tile("Distanza", fmtKm(a.distance_m), "km")}${tile("Tempo", fmtTime(a.moving_s || a.duration_s))}${run ? tile("Ritmo", fmtTime(paceOf(a)), "/km") : ""}${tile("FC media", num(a.avg_hr), "bpm", a.max_hr ? `massima ${num(a.max_hr)}` : "")}</div>
    <div class="grid">
      ${a.stream.length ? card("dPace", "Ritmo", "minuti al km, in alto è più veloce", "s12", true) + card("dHr", "Frequenza cardiaca", "bpm", "s12", true) : `<div class="card s12 muted">Tracciato non disponibile per questa attività.</div>`}
      ${a.zones ? card("dZones", "Tempo per zona", "minuti", "s12", true) : ""}
      ${a.efforts.length ? `<section class="card s5"><h2>Migliori sforzi</h2><table><tbody>${a.efforts.map(e => `<tr><td>${esc(e.label)}</td><td class="r num">${fmtTime(e.time_s)}</td></tr>`).join("")}</tbody></table></section>` : ""}
      ${a.splits.length ? `<section class="card s7"><h2>Split al km</h2><div class="table-wrap"><table><thead><tr><th>Km</th><th class="r">Ritmo</th><th class="r">FC</th><th class="r">D+</th></tr></thead><tbody>${a.splits.map(s => `<tr><td>${s.km}</td><td class="r num">${fmtTime(s.time_s)}</td><td class="r num">${num(s.avg_hr)}</td><td class="r num">${num(s.elev_gain)} m</td></tr>`).join("")}</tbody></table></div></section>` : ""}
    </div></div>`;
  drawer.hidden = false;
  $("#closeDrawer").onclick = closeDrawer;
  $("#closeDrawer").focus();
  if (a.stream.length) {
    const labels = a.stream.map(p => num(p.km, 1) + " km");
    const hasHr = a.stream.some(p => p.hr);
    drawChart("dPace", "line", labels, [{ label: "Ritmo", data: a.stream.map(p => p.pace && p.pace < 900 ? p.pace : null), color: cssVar("--series-1") }], { format: fmtTime, reverse: true, unit: "/km" });
    if (hasHr) drawChart("dHr", "line", labels, [{ label: "FC", data: a.stream.map(p => p.hr), color: cssVar("--series-2") }], { decimals: 0, unit: "bpm" });
  }
  if (a.zones) drawChart("dZones", "bar", a.zones.map(z => z.zone), [{ label: "Minuti", data: a.zones.map(z => z.minutes), color: cssVar("--series-1") }], { unit: "min" });
}
function closeDrawer() { const d = $("#drawer"); d.hidden = true; d.innerHTML = ""; }
$("#drawer").addEventListener("click", e => { if (e.target.id === "drawer") closeDrawer(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

// ---- pagine ---------------------------------------------------------------------------------
async function pageOverview() {
  const d = await api("/api/overview"), s = d.summary;
  if (!s.total_runs) { view.innerHTML = `<div class="head"><h1>Panoramica</h1></div>` + noData(); return; }
  const tsb = s.form?.tsb;
  const formText = tsb == null ? "" : tsb > 5 ? "fresco" : tsb < -20 ? "molto affaticato" : tsb < -8 ? "sotto carico" : "equilibrato";
  view.innerHTML = `<div class="head"><div><h1>Panoramica</h1><p class="muted">Ultima corsa: ${fmtDate(s.last_activity)}</p></div></div>
    <div class="tiles">
      ${tile("Questa settimana", num(s.week_km, 1), "km", `${s.week_runs} corse · media 4 settimane ${num(s.avg_4w_km)} km`)}
      ${tile(`Anno ${s.today.slice(0, 4)}`, num(s.year_km), "km", `${s.year_runs} corse`)}
      ${tile("Forma (TSB)", num(tsb), "", formText)}
      ${tile("VDOT stimato", num(s.vdot, 1), "", "ultimi 120 giorni")}
    </div>
    <div class="grid">
      ${card("week", "Chilometri a settimana", "Ultime 26 settimane", "s7")}
      <section class="card s5"><h2 style="margin-bottom:10px">Consigli</h2>${recCards(d.recommendations.slice(0, 3))}${d.recommendations.length > 3 ? `<p style="margin-top:10px"><a href="#progressi">Vedi tutti (${d.recommendations.length})</a></p>` : ""}</section>
      <section class="card s12"><h2 style="margin-bottom:8px">Ultime corse</h2><div class="table-wrap"><table>${activityHead}<tbody>${activityRows(d.recent)}</tbody></table></div></section>
    </div>`;
  drawChart("week", "bar", d.weekly.map(w => shortDate(w.week)), [{ label: "Km", data: d.weekly.map(w => w.km), color: cssVar("--series-1") }], { unit: "km" });
}

let actState = { kind: "", q: "", offset: 0 };
async function pageActivities() {
  view.innerHTML = `<div class="head"><div><h1>Attività</h1><p class="muted" id="actCount"></p></div>
    <div class="row" style="flex:0 1 auto"><input id="actQ" class="search" type="search" placeholder="Cerca per nome o tipo" aria-label="Cerca attività" value="${esc(actState.q)}"><select id="actKind" class="search" aria-label="Tipo di attività"></select></div></div>
    <section class="card"><div class="table-wrap"><table>${activityHead}<tbody id="actBody"></tbody></table></div>
    <div class="row" style="margin-top:12px;justify-content:space-between"><button class="btn" id="actPrev" style="flex:0 0 auto">Più recenti</button><button class="btn" id="actNext" style="flex:0 0 auto">Più vecchie</button></div></section>`;
  const load = async () => {
    const p = new URLSearchParams({ offset: actState.offset, limit: 30 });
    if (actState.kind) p.set("kind", actState.kind);
    if (actState.q) p.set("q", actState.q);
    const d = await api("/api/activities?" + p);
    $("#actCount").textContent = `${d.total} attività`;
    $("#actBody").innerHTML = activityRows(d.items) || `<tr><td colspan="7" class="muted">Nessun risultato</td></tr>`;
    const select = $("#actKind");
    if (!select.options.length) {
      select.innerHTML = `<option value="">Tutti i tipi</option><option value="run">Solo corse</option>${d.types.map(t => `<option value="${esc(t)}">${esc(typeName(t))}</option>`).join("")}`;
      select.value = actState.kind;
    }
    $("#actPrev").disabled = actState.offset === 0;
    $("#actNext").disabled = actState.offset + 30 >= d.total;
  };
  let timer;
  $("#actQ").oninput = e => { clearTimeout(timer); timer = setTimeout(() => { actState.q = e.target.value; actState.offset = 0; load(); }, 250); };
  $("#actKind").onchange = e => { actState.kind = e.target.value; actState.offset = 0; load(); };
  $("#actPrev").onclick = () => { actState.offset = Math.max(0, actState.offset - 30); load(); };
  $("#actNext").onclick = () => { actState.offset += 30; load(); };
  await load();
}

const RANGES = [[12, "12 sett."], [26, "6 mesi"], [52, "1 anno"], [260, "5 anni"]];
let progressWeeks = 26;
async function pageProgress() {
  const [d, recs] = await Promise.all([api(`/api/progress?weeks=${progressWeeks}`), api("/api/recommendations")]);
  destroyCharts();
  const p = d.predictions;
  view.innerHTML = `<div class="head"><div><h1>Progressi</h1><p class="muted">Carico, efficienza e previsioni. CTL, ATL, TSB e VDOT sono stime.</p></div>
    <div class="chips">${RANGES.map(([w, l]) => `<button class="chip" data-weeks="${w}" aria-pressed="${w === progressWeeks}">${l}</button>`).join("")}</div></div>
    <div class="grid">
      ${card("load", "Fitness, fatica e forma", "CTL 42 giorni, ATL 7 giorni, TSB = differenza", "s12")}
      ${card("fit", "VO₂ stimato", "miglior VDOT mensile e VO₂max Garmin", "s6")}
      ${card("ef", "Efficienza aerobica", "metri al minuto per battito, corse facili. Se sale, a pari FC vai più forte", "s6")}
      ${card("zones", "Intensità settimanale", `minuti per zona, FC massima ${num(d.zones.max_hr)} bpm`, "s7")}
      ${card("months", "Chilometri al mese", "", "s5")}
      <section class="card s6"><h2 style="margin-bottom:6px">Previsioni gara</h2>${p ? `<p class="muted small">Da ${esc(p.based_on.distance)} in ${fmtTime(p.based_on.time_s)} del ${fmtDate(p.based_on.date)}. VDOT ${num(p.vdot, 1)}</p>
        <table><tbody>${p.races.map(r => `<tr><td>${esc(r.label)}</td><td class="r num">${fmtTime(r.time_s)}</td><td class="r num muted">${fmtPace(r.time_s / r.distance_m * 1000)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">Serve almeno un 5 km negli ultimi 120 giorni.</p>`}</section>
      <section class="card s6"><h2 style="margin-bottom:6px">Ritmi di allenamento</h2>${p ? `<table><tbody>${p.paces.map(x => `<tr><td>${esc(x.name)}</td><td class="r num">${fmtTime(x.to_s)} – ${fmtTime(x.from_s)} /km</td></tr>`).join("")}</tbody></table>` : `<p class="muted">Dati insufficienti.</p>`}</section>
      <section class="card s12"><h2 style="margin-bottom:10px">Tutti i consigli</h2>${recCards(recs)}</section>
    </div>`;
  document.querySelectorAll("[data-weeks]").forEach(b => b.onclick = () => { progressWeeks = +b.dataset.weeks; pageProgress(); });
  const c1 = cssVar("--series-1"), c2 = cssVar("--series-2"), c3 = cssVar("--series-3");
  drawChart("load", "line", d.load.map(x => shortDate(x.date)), [
    { label: "Fitness (CTL)", data: d.load.map(x => x.ctl), color: c1 }, { label: "Fatica (ATL)", data: d.load.map(x => x.atl), color: c2 }, { label: "Forma (TSB)", data: d.load.map(x => x.tsb), color: c3 }], { decimals: 0, zero: false });
  drawChart("fit", "line", d.fitness.map(x => monthLabel(x.month)), [{ label: "VDOT", data: d.fitness.map(x => x.vdot), color: c1, points: true }, { label: "VO₂max Garmin", data: d.fitness.map(x => x.garmin_vo2), color: c2, points: true }], { zero: false });
  drawChart("ef", "line", d.efficiency.map(x => shortDate(x.week)), [{ label: "Efficienza", data: d.efficiency.map(x => x.ef), color: c1 }], { decimals: 2, zero: false });
  const seq = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5"].map(cssVar);
  drawChart("zones", "bar", d.zones.weeks.map(x => shortDate(x.week)), d.zones.zones.map((z, i) => ({ label: z, data: d.zones.weeks.map(w => w.minutes[i]), color: seq[i] })), { stacked: true, decimals: 0, unit: "min" });
  drawChart("months", "bar", d.monthly.map(x => monthLabel(x.month)), [{ label: "Km", data: d.monthly.map(x => x.km), color: c1 }], { decimals: 0, unit: "km" });
}

let lbDistance = 5000;
async function pageLeaderboard() {
  const d = await api(`/api/leaderboard?distance=${lbDistance}`);
  destroyCharts();
  const label = d.distances.find(x => x.m === lbDistance)?.label;
  const r = d.records;
  view.innerHTML = `<div class="head"><div><h1>Classifica tempi</h1><p class="muted">Il miglior tratto di ogni corsa, ricavato dai dati GPS. Scegli una distanza.</p></div></div>
    ${r.personal.length ? `<div class="prs" style="margin-bottom:14px">${r.personal.map(p => `<button class="pr" data-distance="${p.distance_m}" aria-pressed="${p.distance_m === lbDistance}"><div class="muted small">${esc(p.label)}</div><div class="t">${fmtTime(p.time_s)}</div><div class="muted small">${fmtDate(p.date)}</div></button>`).join("")}</div>` : noData()}
    ${r.personal.length ? `<div class="grid">
      <section class="card s7"><h2 style="margin-bottom:8px">Top ${d.items.length} · ${esc(label)}</h2>${d.items.length ? `<div class="table-wrap"><table><thead><tr><th>#</th><th class="r">Tempo</th><th class="r">Ritmo</th><th>Data</th><th>Corsa</th></tr></thead><tbody>
        ${d.items.map(i => `<tr class="click" data-activity="${esc(i.activity_id)}" tabindex="0"><td><span class="rank ${i.rank === 1 ? "first" : ""}">${i.rank}</span></td><td class="r num"><strong>${fmtTime(i.time_s)}</strong></td><td class="r num">${fmtPace(i.pace_s)}</td><td>${fmtDate(i.date)}</td><td class="wrap">${esc(i.name || "Corsa")}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">Nessuna corsa copre questa distanza.</p>`}</section>
      <div class="s5" style="display:grid;gap:14px;align-content:start">
        ${d.progression.length > 1 ? card("prog", "Evoluzione del record", "in alto è più veloce", "") : ""}
        <section class="card"><h2 style="margin-bottom:6px">Altri primati</h2><table><tbody>
          ${r.longest ? `<tr><td>Corsa più lunga</td><td class="r num">${num(r.longest.km, 1)} km</td></tr>` : ""}
          ${r.biggest_week ? `<tr><td>Settimana più ricca</td><td class="r num">${num(r.biggest_week.km, 1)} km</td></tr>` : ""}
          <tr><td>Corse totali</td><td class="r num">${r.total_runs}</td></tr></tbody></table></section>
      </div></div>` : ""}`;
  document.querySelectorAll("[data-distance]").forEach(b => b.onclick = () => { lbDistance = +b.dataset.distance; pageLeaderboard(); });
  if (d.progression.length > 1) drawChart("prog", "line", d.progression.map(x => shortDate(x.date)), [{ label: "Record", data: d.progression.map(x => x.time_s), color: cssVar("--series-1"), step: true, points: true }], { format: fmtTime, reverse: true, zero: false });
}

const HEALTH_SERIES = [
  ["sleep_h", "Sonno", "ore a notte", "bar", { unit: "h" }], ["hrv", "HRV", "ms, media notturna", "line", { decimals: 0, zero: false, unit: "ms" }],
  ["resting_hr", "FC a riposo", "bpm", "line", { decimals: 0, zero: false, unit: "bpm" }], ["steps", "Passi", "al giorno", "bar", { decimals: 0 }],
  ["stress", "Stress", "media giornaliera", "line", { decimals: 0, zero: false }], ["sleep_score", "Punteggio sonno", "0-100", "line", { decimals: 0, zero: false }],
  ["bb_high", "Body Battery massimo", "0-100", "line", { decimals: 0, zero: false }], ["weight_kg", "Peso", "kg", "line", { zero: false, unit: "kg" }],
  ["spo2", "SpO₂", "%", "line", { decimals: 0, zero: false, unit: "%" }], ["calories", "Calorie", "kcal al giorno", "bar", { decimals: 0 }],
];
let healthDays = 90;
async function pageHealth() {
  const rows = await api(`/api/health?days=${healthDays}`);
  destroyCharts();
  const avg = key => { const v = rows.slice(-7).map(r => r[key]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const available = HEALTH_SERIES.filter(([k]) => rows.some(r => r[k] != null));
  view.innerHTML = `<div class="head"><div><h1>Salute</h1><p class="muted">Tutto ciò che Garmin raccoglie ogni giorno.</p></div>
    <div class="chips">${[[30, "30 giorni"], [90, "90 giorni"], [365, "1 anno"]].map(([d, l]) => `<button class="chip" data-days="${d}" aria-pressed="${d === healthDays}">${l}</button>`).join("")}</div></div>
    ${available.length ? `<div class="tiles">${tile("Sonno (7 gg)", num(avg("sleep_h"), 1), "h")}${tile("HRV (7 gg)", num(avg("hrv")), "ms")}${tile("FC a riposo (7 gg)", num(avg("resting_hr")), "bpm")}${tile("Passi (7 gg)", num(avg("steps")))}</div>
    <div class="grid">${available.map(([k, t, sub]) => card("h-" + k, t, sub, "s6", true)).join("")}</div>` : empty("Nessun dato di salute ancora: sincronizza dalle Impostazioni.")}`;
  document.querySelectorAll("[data-days]").forEach(b => b.onclick = () => { healthDays = +b.dataset.days; pageHealth(); });
  const color = cssVar("--series-1");
  available.forEach(([k, t, , type, opts]) => drawChart("h-" + k, type, rows.map(r => shortDate(r.date)), [{ label: t, data: rows.map(r => r[k]), color }], opts));
}

// ---- chat -----------------------------------------------------------------------------------
function inline(s) { return s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>"); }
function renderMarkdown(src) {
  const lines = esc(src).split("\n"), out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
      const cells = l => l.trim().replace(/^\||\|$/g, "").split("|").map(c => inline(c.trim()));
      let html = `<div class="table-wrap"><table><thead><tr>${cells(line).map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>`;
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { html += `<tr>${cells(lines[i]).map(c => `<td class="wrap">${c}</td>`).join("")}</tr>`; i++; }
      out.push(html + "</tbody></table></div>");
    } else if (/^#{1,6}\s/.test(line)) { out.push(`<h4>${inline(line.replace(/^#+\s/, ""))}</h4>`); i++; }
    else if (/^\s*[-*]\s/.test(line) || /^\s*\d+\.\s/.test(line)) {
      const ordered = /^\s*\d+\./.test(line), tag = ordered ? "ol" : "ul", re = ordered ? /^\s*\d+\.\s/ : /^\s*[-*]\s/;
      let html = `<${tag}>`;
      while (i < lines.length && re.test(lines[i])) { html += `<li>${inline(lines[i].replace(re, ""))}</li>`; i++; }
      out.push(html + `</${tag}>`);
    } else if (!line.trim()) i++;
    else { out.push(`<p>${inline(line)}</p>`); i++; }
  }
  return out.join("");
}
const TOOL_LABELS = { get_summary: "riepilogo", get_recommendations: "consigli", list_activities: "attività", get_activity: "dettaglio attività", get_weekly_volume: "volume", get_training_load: "carico", get_efficiency_and_zones: "efficienza e zone", get_records_and_leaderboard: "record", get_race_predictions: "previsioni", get_health: "salute" };
const SUGGESTIONS = ["Come sta andando il mio allenamento?", "Preparami la prossima settimana", "Sono a rischio sovraccarico?", "Come posso migliorare sui 10 km?"];

function storedConversation() { try { return localStorage.getItem("conversation"); } catch { return null; } }
function storeConversation(id) { try { localStorage.setItem("conversation", id); } catch { /* opzionale */ } }

async function pageCoach() {
  const status = await api("/api/status");
  let conversation = storedConversation();
  if (!conversation) { conversation = (await post("/api/chat/new")).conversation_id; storeConversation(conversation); }
  const history = await api(`/api/chat/${conversation}`);
  view.innerHTML = `<div class="head"><div><h1>Coach AI</h1><p class="muted">Claude legge i tuoi dati e risponde con numeri alla mano.</p></div><button class="btn" id="newChat">Nuova conversazione</button></div>
    <section class="card chat"><div class="messages" id="msgs" aria-live="polite"></div>
    <div class="suggest chips" id="suggest"></div>
    <form class="composer" id="composer"><textarea id="prompt" placeholder="Chiedi al tuo coach…" aria-label="Messaggio" ${status.has_anthropic_key ? "" : "disabled"}></textarea><button class="btn primary" ${status.has_anthropic_key ? "" : "disabled"}>Invia</button></form></section>`;
  const msgs = $("#msgs");
  const add = (role, html) => { const el = document.createElement("div"); el.className = "msg " + role; el.innerHTML = html; msgs.append(el); msgs.scrollTop = msgs.scrollHeight; return el; };
  history.forEach(m => add(m.role, m.role === "user" ? esc(m.text) : renderMarkdown(m.text)));
  if (!status.has_anthropic_key) add("assistant", `<p>Per chattare serve una chiave API Anthropic: aggiungila in <a href="#impostazioni">Impostazioni</a>.</p>`);
  const suggest = $("#suggest");
  if (!history.length && status.has_anthropic_key) suggest.innerHTML = SUGGESTIONS.map(s => `<button type="button" class="chip">${esc(s)}</button>`).join("");
  $("#newChat").onclick = async () => { const r = await post("/api/chat/new"); storeConversation(r.conversation_id); pageCoach(); };

  let busy = false;
  async function send(text) {
    if (busy || !text.trim()) return;
    busy = true; suggest.innerHTML = "";
    add("user", esc(text));
    const answer = add("assistant", `<span class="toolnote">Sto pensando…</span>`);
    let buffer = "", full = "";
    try {
      const res = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: conversation, message: text }) });
      if (!res.ok || !res.body) throw new Error(`Errore ${res.status}`);
      const reader = res.body.getReader(), decoder = new TextDecoder();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (const part of parts) {
          if (!part.startsWith("data: ")) continue;
          const ev = JSON.parse(part.slice(6));
          if (ev.type === "delta") { full += ev.text; answer.innerHTML = renderMarkdown(full); }
          else if (ev.type === "tool") { if (!full) answer.innerHTML = `<span class="toolnote">Leggo: ${esc(TOOL_LABELS[ev.name] || ev.name)}…</span>`; }
          else if (ev.type === "error") { answer.classList.add("err"); answer.innerHTML = `<p>${esc(ev.message)}</p>`; }
          msgs.scrollTop = msgs.scrollHeight;
        }
      }
    } catch (err) { answer.classList.add("err"); answer.textContent = err.message; }
    busy = false;
  }
  suggest.onclick = e => { if (e.target.matches(".chip")) send(e.target.textContent); };
  const prompt = $("#prompt");
  $("#composer").onsubmit = e => { e.preventDefault(); const t = prompt.value; prompt.value = ""; send(t); };
  prompt.onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } };
}

// ---- impostazioni ---------------------------------------------------------------------------
function parseDuration(text) {
  const parts = text.trim().split(":").map(Number);
  if (!text.trim() || parts.some(Number.isNaN) || parts.length > 3) return null;
  return parts.reduce((total, part) => total * 60 + part, 0);
}
async function pageSettings() {
  const [s, st] = await Promise.all([api("/api/settings"), api("/api/status")]);
  const secretRow = (name, label, has, hint) => `<form class="form" data-secret="${name}"><label>${esc(label)} ${has ? `<span class="ok">✓ salvata</span>` : ""}
    <input type="password" autocomplete="off" placeholder="${has ? "Incolla per sostituirla" : "Incolla la chiave"}" name="value"></label>
    <p class="muted small">${hint}</p><div class="row"><button class="btn primary" style="flex:0 0 auto">Salva</button>${has ? `<button type="button" class="btn" data-clear style="flex:0 0 auto">Rimuovi</button>` : ""}</div></form>`;
  view.innerHTML = `<div class="head"><div><h1>Impostazioni</h1><p class="muted">Dati e chiavi restano su questo PC${st.demo ? "" : `, in ${esc(st.data_dir)}`}.</p></div></div>
    <div class="grid">
      <section class="card s6"><h2 style="margin-bottom:12px">Collegamento Garmin</h2>
        ${secretRow("garmin_api_key", "Garmin Personal API key", st.has_garmin_key, `Creala su <a href="https://connect.garmin.com/app/settings/personalApiToken" target="_blank" rel="noopener">Garmin Connect → Personal API</a>. Viene mostrata una sola volta.`)}
        <p class="small muted" style="margin-top:12px">Nel database: ${st.counts.activities} attività, ${st.counts.runs} corse, ${st.counts.days} giorni di salute.${st.last_sync ? ` Ultima sincronizzazione: ${esc(st.last_sync.replace("T", " "))}.` : ""}</p></section>
      <section class="card s6"><h2 style="margin-bottom:12px">Claude</h2>
        ${secretRow("anthropic_api_key", "Chiave API Anthropic", st.has_anthropic_key, `Da <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">console.anthropic.com</a>. Il consumo si paga a parte rispetto all'abbonamento Claude.`)}
        <form class="form" id="modelForm" style="margin-top:12px"><label>Modello della chat<select name="model">${Object.entries(s.models).map(([id, n]) => `<option value="${id}" ${id === s.model ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label></form></section>
      <section class="card s6"><h2 style="margin-bottom:12px">Atleta e obiettivo</h2><form class="form" id="profile">
        <div class="row"><label>FC massima (ora ${num(s.effective_max_hr)})<input name="max_hr" type="number" min="100" max="240" value="${s.max_hr ?? ""}" placeholder="Automatica"></label>
        <label>FC a riposo (ora ${num(s.effective_resting_hr)})<input name="resting_hr" type="number" min="25" max="110" value="${s.resting_hr ?? ""}" placeholder="Automatica"></label></div>
        <div class="row"><label>Distanza gara<select name="distance"><option value="">Nessun obiettivo</option>${[[5000, "5 km"], [10000, "10 km"], [21097, "Mezza maratona"], [42195, "Maratona"]].map(([m, l]) => `<option value="${m}" ${s.goal?.distance_m === m ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label>Data gara<input name="date" type="date" value="${s.goal?.date ?? ""}"></label>
        <label>Tempo obiettivo<input name="time" placeholder="h:mm:ss" value="${s.goal ? fmtTime(s.goal.time_s) : ""}"></label></div>
        <div class="row"><button class="btn primary" style="flex:0 0 auto">Salva</button><span class="muted small" id="profileMsg" role="status"></span></div></form></section>
      <section class="card s6"><h2 style="margin-bottom:8px">Importa file</h2>
        <p class="muted small" style="margin-bottom:10px">Se la Personal API non restituisce le corse più vecchie, carica file .FIT o l'archivio ZIP di <a href="https://www.garmin.com/account/datamanagement/" target="_blank" rel="noopener">Garmin Esporta dati</a>. I doppioni vengono riconosciuti.</p>
        <form id="importForm" class="row"><input type="file" name="files" multiple accept=".fit,.zip" aria-label="File da importare"><button class="btn" style="flex:0 0 auto">Importa</button></form>
        <p class="small" id="importMsg" role="status" style="margin-top:8px"></p></section>
    </div>`;
  document.querySelectorAll("[data-secret]").forEach(form => {
    const name = form.dataset.secret;
    form.onsubmit = async e => { e.preventDefault(); const value = form.elements.value.value; if (!value.trim()) return; await post("/api/secrets", { name, value }); pageSettings(); };
    const clear = $("[data-clear]", form);
    if (clear) clear.onclick = async () => { await post("/api/secrets", { name, value: "" }); pageSettings(); };
  });
  $("#modelForm select").onchange = async e => { await post("/api/settings", { max_hr: s.max_hr, resting_hr: s.resting_hr, goal: s.goal, model: e.target.value }); };
  $("#profile").onsubmit = async e => {
    e.preventDefault();
    const f = e.target.elements, msg = $("#profileMsg");
    let goal = null;
    if (f.distance.value) {
      const time = parseDuration(f.time.value);
      if (!f.date.value || !time) { msg.textContent = "Per l'obiettivo servono data e tempo (h:mm:ss)."; return; }
      goal = { distance_m: +f.distance.value, date: f.date.value, time_s: time };
    }
    await post("/api/settings", { max_hr: f.max_hr.value ? +f.max_hr.value : null, resting_hr: f.resting_hr.value ? +f.resting_hr.value : null, goal, model: $("#modelForm select").value });
    msg.textContent = "Salvato.";
  };
  $("#importForm").onsubmit = async e => {
    e.preventDefault();
    const data = new FormData(e.target), msg = $("#importMsg");
    if (!e.target.elements.files.files.length) return;
    msg.textContent = "Caricamento…";
    try { await api("/api/import", { method: "POST", body: data }); msg.textContent = "Import avviato: segui l'avanzamento a sinistra."; pollStatus(); }
    catch (err) { msg.textContent = err.message; }
  };
}

// ---- navigazione e stato ---------------------------------------------------------------------
const PAGES = { panoramica: ["Panoramica", pageOverview], attivita: ["Attività", pageActivities], progressi: ["Progressi", pageProgress], classifica: ["Classifica", pageLeaderboard], salute: ["Salute", pageHealth], coach: ["Coach AI", pageCoach], impostazioni: ["Impostazioni", pageSettings] };
$("#nav").innerHTML = Object.entries(PAGES).map(([k, [l]]) => `<button data-page="${k}">${l}</button>`).join("");
$("#nav").onclick = e => { const b = e.target.closest("[data-page]"); if (b) location.hash = b.dataset.page; };

async function route() {
  const [hashPage, hashArg] = location.hash.slice(1).split("/");
  const key = PAGES[hashPage] ? hashPage : "panoramica";
  document.querySelectorAll("#nav button").forEach(b => b.dataset.page === key ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"));
  destroyCharts();
  try {
    await PAGES[key][1]();
    if (key === "attivita" && hashArg) await openActivity(decodeURIComponent(hashArg));
  }
  catch (err) { view.innerHTML = empty(esc(err.message)); }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", route);

let polling = false;
async function pollStatus() {
  const st = await api("/api/status");
  const job = st.job, text = $("#syncText"), btn = $("#syncBtn");
  $("#demoBanner").hidden = !st.demo;
  btn.disabled = job.running || st.demo;
  if (st.demo) text.textContent = "Sincronizzazione disattivata in modalità demo.";
  else if (job.running) text.textContent = job.message + (job.total ? ` (${Math.round(job.done / job.total * 100)}%)` : "");
  else if (job.finished_at) text.textContent = job.message + (job.warnings.length ? ` Avvisi: ${job.warnings.slice(0, 3).join("; ")}` : "");
  else text.textContent = st.last_sync ? `Ultima: ${st.last_sync.replace("T", " ").slice(0, 16)}` : (st.has_garmin_key ? "Pronto a sincronizzare." : "Chiave Garmin mancante.");
  if (job.running) { if (!polling) { polling = true; setTimeout(() => { polling = false; pollStatus().then(() => { if (!$("#syncBtn").disabled) route(); }); }, 2000); } }
}
$("#syncBtn").onclick = async () => {
  try { await post("/api/sync"); pollStatus(); }
  catch (err) { $("#syncText").textContent = err.message; }
};

pollStatus();
route();
