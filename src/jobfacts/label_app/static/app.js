/* jobfacts labelling page. Plain JS, no build step. Talks only to the JSON API in app.py
   (documented in docs/labelling.md). */
"use strict";

// ---------------------------------------------------------------- constants

const ORDER = [
  "relevant", "work_mode", "remote_from_switzerland", "office_locations",
  "german", "french", "min_years", "title_level",
];
const LABEL = {
  relevant: "Relevant", work_mode: "Work mode", remote_from_switzerland: "Remote from CH",
  office_locations: "Office locations", german: "German", french: "French",
  min_years: "Min. years", title_level: "Title level",
};
const ABBR = {
  work_mode: "MODE", remote_from_switzerland: "RCH", office_locations: "LOC",
  german: "DE", french: "FR", min_years: "YRS", title_level: "TTL",
};
const RELEVANT_DEFS = {
  true: "An AI, ML, data or research role: the kind of role the search keeps.",
  false: "Not such a role.",
  unsure: "Cannot tell from the posting.",
};
// Short reminders of the rules in docs/fields.md, shown under the question.
const HINTS = {
  relevant: "Is this an AI, ML, data or research role?",
  work_mode: "Occasional travel or offsites still count as remote.",
  remote_from_switzerland: "Only for remote roles; any other work mode is not_applicable.",
  office_locations: "Offices for this role only. Toggle candidates (1–9, 0 = outside CH), add others with /. Leave empty when no office is named, then Enter.",
  german: "A posting written in German does not by itself require German. “German or French” → either_required in both.",
  french: "A posting written in French does not by itself require French. “German or French” → either_required in both.",
  min_years: "A range uses the minimum (“3–5 years” → 3). “PhD or 3 years” → 3. Type the number; N = not stated, + = 15_plus.",
  title_level: "From the title (T) only. Lead, head of, staff, principal → staff_principal.",
};
const TITLE_ID = 0;
const LOCATION_ID = -1;

// ---------------------------------------------------------------- state

const S = {
  spec: null,          // /api/spec
  specBy: {},          // field name -> spec field
  items: [],           // /api/items
  progress: null,
  item: null,          // /api/items/{id}
  f: null,             // form state, see emptyForm()
  snapshot: "",        // serialized form after load/save, for "unsaved changes"
  active: 0,           // index into ORDER
  hover: null,         // field name hovered in the form
  errors: {},          // field name -> message (client or 422)
  sentEls: new Map(),  // sentence id -> element
  ybuf: "", ytimer: null,
  yhover: null,
  locQuery: "",
  noteOpen: {},        // field name (or "_posting") -> bool
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sid = (i) => (i === TITLE_ID ? "T" : i === LOCATION_ID ? "L" : String(i));
const activeName = () => ORDER[S.active];

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  return { status: res.status, data };
}

let toastTimer = null;
function toast(msg) {
  let el = $(".toast");
  if (!el) { el = document.createElement("div"); el.className = "toast"; document.body.append(el); }
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 1800);
}

function globalMsg(msg, kind = "info") {
  const el = $("#global-msg");
  el.textContent = msg || "";
  el.className = "global-msg " + kind;
}

// ---------------------------------------------------------------- form model

function emptyForm() {
  const f = {};
  for (const name of ORDER) {
    f[name] = { value: null, sentences: new Set(), note: "", status: "empty" };
  }
  f.office_locations.keys = new Set();
  f._note = "";
  return f;
}

function formFromItem(item) {
  const f = emptyForm();
  const label = item.label;
  if (label) {
    f.relevant.value = label.relevant;
    f.relevant.status = "set";
    for (const [name, e] of Object.entries(label.fields || {})) {
      if (!f[name]) continue;
      Object.assign(f[name], { value: e.value, sentences: new Set(e.sentences || []), note: e.note || "", status: "set" });
    }
    f.office_locations.keys = new Set(label.office_locations || []);
    f.office_locations.sentences = new Set(label.office_locations_sentences || []);
    f.office_locations.status = "set";
    f._note = label.note || "";
  } else if (item.prefill) {
    for (const [name, e] of Object.entries(item.prefill.fields || {})) {
      if (!f[name]) continue;
      Object.assign(f[name], { value: e.value, sentences: new Set(e.sentences || []), status: "suggested" });
    }
    if (item.prefill.office_locations) {
      f.office_locations.keys = new Set(item.prefill.office_locations);
      f.office_locations.sentences = new Set(item.prefill.office_locations_sentences || []);
      f.office_locations.status = "suggested";
    }
  }
  return f;
}

function serialize(f) {
  const out = { note: f._note };
  for (const name of ORDER) {
    const x = f[name];
    out[name] = [x.value, [...x.sentences].sort((a, b) => a - b), x.note, x.status,
      x.keys ? [...x.keys].sort() : null];
  }
  return JSON.stringify(out);
}
const isDirty = () => S.f && serialize(S.f) !== S.snapshot;

function valueSpec(name, value) {
  const sp = S.specBy[name];
  return sp ? sp.values.find((v) => v.value === value) : null;
}
// Does the current value of this field take supporting sentences?
function takesSentences(name) {
  if (name === "relevant") return false;
  if (name === "office_locations") return S.f.office_locations.keys.size > 0;
  const v = S.f[name].value;
  if (v === null) return true; // evidence may be marked before the value is picked
  const vs = valueSpec(name, v);
  return vs ? vs.needs_sentence : false;
}
function needsSentence(name) {
  if (name === "relevant") return false;
  if (name === "office_locations") return S.f.office_locations.keys.size > 0;
  const vs = valueSpec(name, S.f[name].value);
  return vs ? vs.needs_sentence : false;
}

// Client-side check of one field; returns an error message or null.
function checkField(name) {
  const x = S.f[name];
  if (name !== "office_locations" && x.value === null) return "Pick a value.";
  if (needsSentence(name) && x.sentences.size === 0) {
    return "Mark at least one supporting sentence (click it on the left).";
  }
  return null;
}

// Soft consistency warnings (never block saving).
function warning(name) {
  const wm = S.f.work_mode.value;
  const rch = S.f.remote_from_switzerland.value;
  if (name === "remote_from_switzerland" && wm && rch) {
    if (wm !== "remote" && rch !== "not_applicable") return `Work mode is ${wm}: this is normally not_applicable.`;
    if (wm === "remote" && rch === "not_applicable") return "Work mode is remote: not_applicable does not fit.";
  }
  if (name === "work_mode" && wm === "remote" && rch === "not_applicable" && S.f.remote_from_switzerland.status === "set") {
    return "Remote from CH is not_applicable: check it again.";
  }
  return null;
}

// ---------------------------------------------------------------- cue words

const NUM = "(?:\\d+(?:[.,]\\d)?|one|two|three|four|five|six|seven|eight|nine|ten|ein|eine|einem|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|deux|trois|quatre|cinq|sept|huit|neuf|dix)";
const CUES = [
  ["years", `${NUM}(?:\\s*(?:\\+|-|–|to|bis|à|a)\\s*${NUM})?\\s*\\+?\\s*(?:\\(\\s*\\)\\s*)?(?:years?|yrs?|jahren?|jahre|ans?|années?)`, true],
  ["years", "(?:berufs|praxis|projekt|führungs|industrie)?erfahrung\\p{L}*|experience[ds]?|expérience\\p{L}*|years?|jahren?|jahre|ans|années?|mindestens|at least|minimum", false],
  ["de", "deutsch(?!land)\\p{L}*|german|allemand", true],
  ["fr", "französisch\\p{L}*|franzoesisch\\p{L}*|french|français\\p{L}*|francais\\p{L}*|francophone", true],
  ["level", "fluent(?:ly)?|fluency|fließend|fliessend|flüssig|courant|couramment|native|mother tongue|muttersprach\\p{L}*|langue maternelle|verhandlungssicher|proficien\\p{L}*|[ABC][12]|bilingu\\p{L}*|(?:sehr )?gute(?:n)? (?:deutsch|französisch|englisch)?kenntnisse|bonnes? connaissances?|maîtrise|working knowledge|von vorteil|un atout|atout|nice to have|advantage(?:ous)?|a plus|is a plus|preferred|wünschenswert|souhaité\\p{L}*|required|erforderlich|vorausgesetzt|zwingend|exigée?s?|indispensable", false],
  ["mode", "remote\\p{L}*|hybrid\\p{L}*|hybride|home[- ]?office|télétravail|teletravail|mobile[sn]? arbeiten|work(?:ing)? from home|wfh|on[- ]?site|vor ort|sur site|présentiel|in[- ]office|office[- ]based|flexible work\\p{L}*|flexibles arbeiten", true],
  ["region", "europe\\p{L}*|europa|européen\\p{L}*|EU|EEA|EWR|EMEA|DACH|CET|CEST|time ?zones?|zeitzone\\p{L}*|fuseaux? horaires?|anywhere|worldwide|weltweit", false],
  ["ch", "switzerland|schweiz\\p{L}*|suisse\\p{L}*|svizzera|swiss", true],
  ["office", "offices?|büro\\p{L}*|standort\\p{L}*|bureaux?|based in|located in|arbeitsort|lieu de travail|headquarter\\p{L}*|HQ|campus", false],
];
const TITLE_CUES = "senior|sr\\.?|junior|jr\\.?|lead|staff|principal|head|chief|director|intern\\p{L}*|praktik\\p{L}*|werkstudent\\p{L}*|stagiaire|stage|thesis|graduate|entry[- ]level|trainee|apprenti\\p{L}*|II|III|IV";

function rx(src) {
  return new RegExp(`(?<![\\p{L}\\p{N}])(?:${src})(?![\\p{L}\\p{N}])`, "giu");
}
const CUE_RX = CUES.map(([fam, src, core]) => [fam, rx(src), core]);
const TITLE_RX = rx(TITLE_CUES);

function cueHtml(text, id, candRx) {
  const hits = [];
  const add = (fam, re, core) => {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text))) {
      if (!m[0]) { re.lastIndex++; continue; }
      hits.push([m.index, m.index + m[0].length, fam, core]);
    }
  };
  for (const [fam, re, core] of CUE_RX) add(fam, re, core);
  if (candRx) add("cand", candRx, true);
  if (id === TITLE_ID) add("title", TITLE_RX, false);
  hits.sort((a, b) => a[0] - b[0] || (b[1] - b[0]) - (a[1] - a[0]));
  let out = "", pos = 0;
  for (const [s, e, fam, core] of hits) {
    if (s < pos) continue;
    out += esc(text.slice(pos, s));
    out += `<span class="${core ? "cue " : ""}cue-${fam}">${esc(text.slice(s, e))}</span>`;
    pos = e;
  }
  return out + esc(text.slice(pos));
}

// ---------------------------------------------------------------- posting pane

function renderPosting() {
  const it = S.item;
  const pane = $("#posting");
  const matched = [...new Set((it.location_candidates || []).map((c) => c.matched).filter(Boolean))]
    .sort((a, b) => b.length - a.length)
    .map((m) => m.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const candRx = matched.length ? rx(matched.join("|")) : null;
  const rows = [
    { id: TITLE_ID, text: it.title, cls: "meta title" },
    { id: LOCATION_ID, text: it.location_raw || "", cls: "meta loc meta-last" },
    ...it.sentences.map((s) => ({ id: s.id, text: s.text, cls: "" })),
  ];
  const inner = document.createElement("div");
  inner.className = "posting-inner";
  inner.innerHTML = `<div class="employer">${esc(it.employer)} · posting ${it.posting_id}</div>`;
  S.sentEls = new Map();
  for (const r of rows) {
    const el = document.createElement("div");
    el.className = "sent " + r.cls;
    el.dataset.id = r.id;
    const body = r.text ? cueHtml(r.text, r.id, candRx) : '<span style="color:var(--faint)">(no location field)</span>';
    el.innerHTML = `<span class="num">${sid(r.id)}</span><span class="txt">${body}</span><span class="marks"></span>`;
    inner.append(el);
    S.sentEls.set(r.id, el);
    if (r.id === LOCATION_ID) {
      const sep = document.createElement("div");
      sep.className = "sep";
      inner.append(sep);
    }
  }
  pane.replaceChildren(inner);
}

function updateMarks() {
  const act = activeName();
  document.body.dataset.active = act;
  for (const [id, el] of S.sentEls) {
    el.classList.remove("ev-set", "ev-sugg", "ev-hover");
    const marks = [];
    for (const name of ORDER) {
      if (name === "relevant") continue;
      const x = S.f[name];
      if (!x.sentences.has(id)) continue;
      const sugg = x.status === "suggested";
      if (name === act) el.classList.add(sugg ? "ev-sugg" : "ev-set");
      if (name === S.hover && name !== act) el.classList.add("ev-hover");
      marks.push(`<span class="mark${sugg ? " sugg" : ""}${name === act ? " active" : ""}" data-field="${name}" title="${LABEL[name]}${sugg ? " (suggested)" : ""}">${ABBR[name]}</span>`);
    }
    el.querySelector(".marks").innerHTML = marks.join("");
  }
}

function scrollToSentence(id, flash = true) {
  const el = S.sentEls.get(id);
  if (!el) return;
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  if (flash) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }
}

function inView(el) {
  const r = el.getBoundingClientRect();
  return r.top >= 56 && r.bottom <= window.innerHeight - 8;
}

function onSentenceClick(ev) {
  const markEl = ev.target.closest(".mark");
  if (markEl) { setActive(ORDER.indexOf(markEl.dataset.field)); return; }
  const el = ev.target.closest(".sent");
  if (!el || !S.f) return;
  if (String(window.getSelection() || "").trim()) return; // selecting text, not clicking
  const id = Number(el.dataset.id);
  const name = activeName();
  const x = S.f[name];
  if (name === "relevant") { toast("Relevant takes no evidence. Move to a field with J."); return; }
  if (!takesSentences(name)) {
    if (name === "office_locations") toast("Add a location first, then mark its sentence.");
    else toast(`${x.value} takes no sentences. Pick another value first.`);
    return;
  }
  if (x.sentences.has(id)) x.sentences.delete(id); else x.sentences.add(id);
  if (x.status === "suggested" || (x.status === "empty" && x.value !== null)) x.status = "set";
  delete S.errors[name];
  refresh();
}

// ---------------------------------------------------------------- form pane

const CANTONS = {
  AG: "Aargau", AI: "Appenzell Innerrhoden", AR: "Appenzell Ausserrhoden", BE: "Bern",
  BL: "Basel-Landschaft", BS: "Basel-Stadt", FR: "Fribourg", GE: "Geneva", GL: "Glarus",
  GR: "Graubünden", JU: "Jura", LU: "Lucerne", NE: "Neuchâtel", NW: "Nidwalden",
  OW: "Obwalden", SG: "St. Gallen", SH: "Schaffhausen", SO: "Solothurn", SZ: "Schwyz",
  TG: "Thurgau", TI: "Ticino", UR: "Uri", VD: "Vaud", VS: "Valais", ZG: "Zug", ZH: "Zürich",
};
function locLabel(key) {
  if (key === "outside_switzerland") return { name: "Outside Switzerland", kind: "" };
  if (key === "country:CH") return { name: "Switzerland", kind: "country" };
  const [kind, ...rest] = key.split(":");
  const name = rest.join(":");
  if (kind === "canton") return { name: `${name}`, kind: `canton ${CANTONS[name] || ""}`.trim() };
  return { name, kind };
}
const norm = (s) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();

function evLinks(x) {
  const ids = [...x.sentences].sort((a, b) => a - b);
  if (!ids.length) return "";
  return "ev " + ids.map((i) => `<a data-goto="${i}">${sid(i)}</a>`).join(", ");
}

function headValue(name) {
  const x = S.f[name];
  if (name === "office_locations") {
    if (x.status === "empty" && !x.keys.size) return { text: "not set", cls: "empty" };
    if (!x.keys.size) return { text: "none named", cls: x.status === "suggested" ? "sugg" : "" };
    return { text: [...x.keys].map((k) => locLabel(k).name).join(", "), cls: x.status === "suggested" ? "sugg" : "" };
  }
  if (x.value === null) return { text: "not set", cls: "empty" };
  return { text: x.value, cls: x.status === "suggested" ? "sugg" : "" };
}

function optionsHtml(name) {
  const x = S.f[name];
  const sugg = x.status === "suggested";
  if (name === "min_years") {
    const vals = S.specBy.min_years.values;
    const num = vals.filter((v) => /^\d+$/.test(v.value));
    const cell = (v, key, wide) => {
      const on = x.value === v.value;
      return `<button class="opt${on ? " on" : ""}${on && sugg ? " sugg" : ""}${wide ? " wide" : ""}" data-value="${esc(v.value)}" title="${esc(v.definition)}">${esc(key)}</button>`;
    };
    const plus = vals.find((v) => v.value === "15_plus");
    const ns = vals.find((v) => v.value === "not_stated");
    const shown = valueSpec("min_years", S.yhover || x.value);
    return `<div class="ygrid">${num.map((v) => cell(v, v.value)).join("")}${cell(plus, "15+", true)}</div>
      <div class="ygrid" style="margin-top:4px">${cell(ns, "N · not_stated", false).replace('class="opt', 'style="grid-column: span 6" class="opt')}</div>
      <div class="ydef">${shown ? `<b>${esc(shown.value)}</b>: ${esc(shown.definition)}` : ""}${S.ybuf ? ` <span style="color:var(--accent)">typing ${esc(S.ybuf)}…</span>` : ""}</div>`;
  }
  const values = name === "relevant"
    ? S.spec.relevant_values.map((v) => ({ value: v, definition: RELEVANT_DEFS[v] || "", needs_sentence: false }))
    : S.specBy[name].values;
  return `<div class="opts">${values.map((v, i) => {
    const on = x.value === v.value;
    const nq = name !== "relevant" && !v.needs_sentence ? '<span class="nq">no sentence</span>' : "";
    return `<button class="opt${on ? " on" : ""}${on && sugg ? " sugg" : ""}" data-value="${esc(v.value)}">
      <span class="k">${i + 1}</span><span class="v">${esc(v.value)}${nq}</span><span class="d">${esc(v.definition)}</span></button>`;
  }).join("")}</div>`;
}

function locCandidates() {
  const cands = (S.item.location_candidates || []).map((c) => ({ ...c }));
  // One quick chip for "outside Switzerland" (key 0) unless it is already a candidate.
  return cands;
}

function locationsHtml() {
  const x = S.f.office_locations;
  const sugg = x.status === "suggested";
  const cands = locCandidates().slice(0, 9);
  const candKeys = new Set(cands.map((c) => c.key));
  const chip = (key, extra, num) => {
    const on = x.keys.has(key);
    const { name, kind } = locLabel(key);
    return `<button class="chip${on ? " on" : ""}${on && sugg ? " sugg" : ""}" data-loc="${esc(key)}">
      ${num !== undefined ? `<span class="k">${num}</span>` : ""}<span>${on ? "✓ " : ""}${esc(name)}</span>
      ${kind ? `<span class="kind">${esc(kind)}</span>` : ""}${extra || ""}</button>`;
  };
  let html = '<div class="loc-sub">Candidates</div><div class="chips">';
  html += cands.length
    ? cands.map((c, i) => chip(c.key, `<span class="where">“${esc(c.matched)}” · ${esc(c.where)}</span>`, i + 1)).join("")
    : '<span class="f-q" style="margin:0">No candidates found.</span>';
  if (!candKeys.has("outside_switzerland")) html += chip("outside_switzerland", "", 0);
  html += "</div>";
  const others = [...x.keys].filter((k) => !candKeys.has(k) && k !== "outside_switzerland");
  if (others.length) {
    html += `<div class="loc-sub">Added</div><div class="chips">${others.map((k) => chip(k, '<span class="where">× remove</span>')).join("")}</div>`;
  }
  html += `<div class="loc-search"><input id="loc-q" type="search" placeholder="Add a location: city, canton (ZH), Switzerland…  ( / )" autocomplete="off" value="${esc(S.locQuery)}"><div class="loc-results" id="loc-results"></div></div>`;
  return html;
}

function locResults() {
  const q = norm(S.locQuery.trim());
  if (!q) return [];
  const x = S.f.office_locations;
  return S.spec.location_keys
    .filter((k) => !x.keys.has(k))
    .map((k) => {
      const { name, kind } = locLabel(k);
      const n = norm(name);
      const score = n === q ? 0 : n.startsWith(q) ? 1 : n.includes(q) || norm(k).includes(q) || norm(kind).includes(q) ? 2 : 9;
      return [score, k];
    })
    .filter(([s]) => s < 9)
    .sort((a, b) => a[0] - b[0] || a[1].localeCompare(b[1]))
    .slice(0, 10)
    .map(([, k]) => k);
}

function renderLocResults() {
  const el = $("#loc-results");
  if (!el) return;
  el.innerHTML = locResults().map((k, i) => {
    const { name, kind } = locLabel(k);
    return `<button class="chip${i === 0 ? " first" : ""}" data-addloc="${esc(k)}"><span>+ ${esc(name)}</span>${kind ? `<span class="kind">${esc(kind)}</span>` : ""}</button>`;
  }).join("");
}

function noteHtml(name) {
  if (name === "relevant" || name === "office_locations") return "";
  const x = S.f[name];
  const open = S.noteOpen[name] || x.note;
  if (!open) return `<button class="note-toggle" data-note="${name}">+ note (E)</button>`;
  return `<div class="note"><textarea data-notefor="${name}" rows="2" placeholder="Note for ${LABEL[name]} (optional)">${esc(x.note)}</textarea></div>`;
}

function renderForm() {
  const focused = document.activeElement;
  const focusId = focused && focused.id;
  const focusNote = focused && focused.dataset ? focused.dataset.notefor : null;
  const body = $("#form-body");
  const act = activeName();
  let html = "";
  ORDER.forEach((name, i) => {
    const x = S.f[name];
    const hv = headValue(name);
    const err = S.errors[name];
    const isAct = name === act;
    html += `<div class="field${isAct ? " active" : ""}${err ? " has-err" : ""}" data-field="${name}" data-idx="${i}">
      <div class="f-head"><span class="f-name">${LABEL[name]}</span>
        <span class="f-val ${hv.cls}">${esc(hv.text)}</span>
        <span class="f-ev">${evLinks(x)}</span>
        <span class="status ${x.status}">${x.status === "set" ? "✓ set" : x.status}</span></div>`;
    if (isAct) {
      const q = name === "relevant" ? "" : name === "office_locations" ? "Which offices does this role work from?" : S.specBy[name].question;
      html += q ? `<div class="f-q">${esc(q)}</div>` : "";
      html += `<div class="f-hint">${esc(HINTS[name])}</div>`;
      html += name === "office_locations" ? locationsHtml() : optionsHtml(name);
      if (x.status === "suggested") html += '<div class="f-warn">Suggested. Check the value and the outlined sentences, then press Enter to confirm.</div>';
    }
    if (err) html += `<div class="f-err">${esc(err)}</div>`;
    const w = warning(name);
    if (w && (isAct || x.status === "set")) html += `<div class="f-warn">⚠ ${esc(w)}</div>`;
    if (isAct) html += noteHtml(name);
    html += "</div>";
  });
  const pOpen = S.noteOpen._posting || S.f._note;
  html += `<div class="post-note">${pOpen
    ? `<div class="note"><textarea id="post-note" rows="2" placeholder="Note for the whole posting (optional)">${esc(S.f._note)}</textarea></div>`
    : '<button class="note-toggle" data-note="_posting">+ note for the posting (Shift+E)</button>'}</div>`;
  body.innerHTML = html;
  if (act === "office_locations") renderLocResults();
  if (focusId && document.getElementById(focusId)) {
    const el = document.getElementById(focusId);
    el.focus();
    if (el.setSelectionRange && typeof el.value === "string") el.setSelectionRange(el.value.length, el.value.length);
  } else if (focusNote) {
    const el = body.querySelector(`textarea[data-notefor="${focusNote}"]`);
    if (el) el.focus();
  }
  // Keep the active field visible inside the form pane (scrollIntoView would also move the page).
  const actEl = body.querySelector(".field.active");
  if (actEl) {
    const top = actEl.offsetTop - body.offsetTop;
    const bottom = top + actEl.offsetHeight;
    if (top < body.scrollTop) body.scrollTop = top - 6;
    else if (bottom > body.scrollTop + body.clientHeight) body.scrollTop = Math.min(top - 6, bottom - body.clientHeight + 6);
  }
}

function renderTop() {
  const it = S.item;
  const idx = S.items.findIndex((x) => x.posting_id === it.posting_id);
  $("#pos").textContent = `#${it.order} · ${idx + 1}/${S.items.length}`;
  const listed = S.items[idx];
  const b = [`<span class="badge lang">${esc(it.language)}</span>`, `<span class="badge">${esc(it.group)}</span>`];
  if (it.mode === "blind") b.push('<span class="badge blind">blind</span>');
  else b.push(`<span class="badge prefilled">${it.prefill && !it.label ? "pre-filled" : "pre-filled mode"}</span>`);
  if (listed && listed.labelled) b.push('<span class="badge done">✓ labelled</span>');
  if (isDirty()) b.push('<span class="badge dirty">● unsaved</span>');
  $("#badges").innerHTML = b.join("");
  const p = S.progress;
  if (p) {
    const bar = (d, t) => `<span class="bar"><i style="width:${t ? (100 * d) / t : 0}%"></i></span>`;
    let h = `<span class="p"><strong>${p.done}/${p.total}</strong>${bar(p.done, p.total)}</span>`;
    for (const lang of ["en", "de", "fr", ...Object.keys(p.by_language).filter((l) => !["en", "de", "fr"].includes(l))]) {
      const r = p.by_language[lang];
      if (r) h += `<span class="p">${lang.toUpperCase()} ${r.done}/${r.total}${bar(r.done, r.total)}</span>`;
    }
    $("#progress").innerHTML = h;
  }
  const allSet = ORDER.every((n) => S.f[n].status === "set");
  $("#save").classList.toggle("blocked", !allSet);
  const nSugg = ORDER.filter((n) => S.f[n].status === "suggested").length;
  $("#confirm-all").style.display = nSugg ? "" : "none";
  $("#confirm-all").textContent = `Confirm all ${nSugg} suggestion${nSugg === 1 ? "" : "s"}`;
}

function refresh() {
  renderForm();
  updateMarks();
  renderTop();
}

// ---------------------------------------------------------------- actions

function setActive(i, { scroll = true } = {}) {
  if (i < 0 || i >= ORDER.length) return;
  S.active = i;
  S.ybuf = "";
  S.yhover = null;
  S.locQuery = "";
  refresh();
  if (scroll) {
    const x = S.f[activeName()];
    const ids = [...x.sentences].sort((a, b) => a - b);
    if (ids.length) {
      const el = S.sentEls.get(ids[0]);
      if (el && !ids.some((id) => inView(S.sentEls.get(id) || el))) scrollToSentence(ids[0], false);
    }
  }
}

function pickValue(name, value) {
  const x = S.f[name];
  x.value = value;
  x.status = "set";
  delete S.errors[name];
  const vs = valueSpec(name, value);
  const noSentence = name === "relevant" || (vs && !vs.needs_sentence);
  if (noSentence) x.sentences.clear();
  // Picking a value that takes no sentence finishes the field: move on.
  if (noSentence && S.active < ORDER.length - 1) setActive(S.active + 1);
  else refresh();
}

function toggleLoc(key) {
  const x = S.f.office_locations;
  if (x.keys.has(key)) x.keys.delete(key); else x.keys.add(key);
  if (!x.keys.size) x.sentences.clear();
  x.status = "set";
  delete S.errors.office_locations;
  refresh();
}

function confirmActive() {
  const name = activeName();
  const x = S.f[name];
  if (name === "office_locations" && x.status === "empty") x.status = "set"; // empty set is a valid answer
  const err = checkField(name);
  if (err) { S.errors[name] = err; refresh(); return; }
  x.status = "set";
  delete S.errors[name];
  if (S.active < ORDER.length - 1) setActive(S.active + 1);
  else {
    refresh();
    if (ORDER.every((n) => S.f[n].status === "set")) globalMsg("All fields set. Ctrl+Enter saves.", "info");
  }
}

function confirmAll() {
  let n = 0;
  const failed = [];
  for (const name of ORDER) {
    if (S.f[name].status !== "suggested") continue;
    const err = checkField(name);
    if (err) { S.errors[name] = err; failed.push(LABEL[name]); continue; }
    S.f[name].status = "set";
    n++;
  }
  toast(`Confirmed ${n} suggestion${n === 1 ? "" : "s"}${failed.length ? `; check ${failed.join(", ")}` : ""}`);
  const firstOpen = ORDER.findIndex((nm) => S.f[nm].status !== "set");
  if (firstOpen >= 0) setActive(firstOpen); else refresh();
}

function buildPayload() {
  const fields = {};
  for (const name of ORDER) {
    if (name === "relevant" || name === "office_locations") continue;
    const x = S.f[name];
    fields[name] = {
      value: x.value,
      sentences: needsSentence(name) ? [...x.sentences].sort((a, b) => a - b) : [],
      note: x.note,
    };
  }
  const loc = S.f.office_locations;
  return {
    relevant: S.f.relevant.value,
    fields,
    office_locations: [...loc.keys].sort(),
    office_locations_sentences: loc.keys.size ? [...loc.sentences].sort((a, b) => a - b) : [],
    note: S.f._note,
  };
}

async function save() {
  S.errors = {}; // server errors from an earlier attempt are re-checked by this save
  const missing = ORDER.filter((n) => S.f[n].status !== "set");
  if (missing.length) {
    globalMsg(`Not saved: ${missing.map((n) => LABEL[n]).join(", ")} still to set or confirm.`, "err");
    setActive(ORDER.indexOf(missing[0]));
    return;
  }
  for (const n of ORDER) {
    const err = checkField(n);
    if (err) S.errors[n] = err;
  }
  const bad = ORDER.filter((n) => S.errors[n]);
  if (bad.length) {
    globalMsg("Not saved: fix the marked fields.", "err");
    setActive(ORDER.indexOf(bad[0]));
    return;
  }
  const { status, data } = await api("PUT", `/api/items/${S.item.posting_id}/label`, buildPayload());
  if (status !== 200) {
    const errs = (data && data.errors) || { body: `HTTP ${status}` };
    S.errors = {};
    const other = [];
    for (const [k, msg] of Object.entries(errs)) {
      const name = k === "office_locations_sentences" ? "office_locations" : k;
      if (ORDER.includes(name)) S.errors[name] = msg; else other.push(`${k}: ${msg}`);
    }
    globalMsg(`Not saved (server said no)${other.length ? ": " + other.join("; ") : "; see the marked fields."}`, "err");
    const first = ORDER.find((n) => S.errors[n]);
    if (first) setActive(ORDER.indexOf(first)); else refresh();
    return;
  }
  S.item.label = data.saved;
  S.snapshot = serialize(S.f);
  const row = S.items.find((x) => x.posting_id === S.item.posting_id);
  if (row) row.labelled = true;
  S.progress = (await api("GET", "/api/progress")).data;
  toast(`Saved posting #${S.item.order}`);
  const next = nextUnlabelled(S.item.order);
  if (next) await loadItem(next.posting_id);
  else { globalMsg("All postings are labelled.", "ok"); refresh(); }
}

function nextUnlabelled(afterOrder) {
  const todo = S.items.filter((x) => !x.labelled);
  return todo.find((x) => x.order > afterOrder) || todo[0] || null;
}

async function goTo(postingId) {
  if (S.item && postingId === S.item.posting_id) return;
  if (isDirty() && !confirm("This posting has unsaved changes. Leave it and lose them?")) {
    history.replaceState(null, "", `#${S.item.posting_id}`);
    return;
  }
  await loadItem(postingId);
}

function step(delta) {
  const idx = S.items.findIndex((x) => x.posting_id === S.item.posting_id);
  const next = S.items[idx + delta];
  if (next) goTo(next.posting_id);
}

async function loadItem(postingId) {
  const { status, data } = await api("GET", `/api/items/${postingId}`);
  if (status !== 200) { toast(`Posting ${postingId} not found`); return; }
  S.item = data;
  S.f = formFromItem(data);
  S.snapshot = serialize(S.f);
  S.errors = {};
  S.noteOpen = {};
  S.locQuery = "";
  globalMsg("");
  history.replaceState(null, "", `#${postingId}`);
  renderPosting();
  window.scrollTo(0, 0);
  setActive(0, { scroll: false });
  if (data.label) globalMsg("Already labelled: showing the saved label. Saving again replaces it.", "info");
  else if (data.mode === "prefilled" && data.prefill) globalMsg("Pre-filled: dashed = suggested. Confirm each field (Enter) or change it.", "info");
  else if (data.mode === "prefilled") globalMsg("Pre-filled mode, but no suggestion exists for this posting.", "info");
}

// ---------------------------------------------------------------- jump list

let jumpSel = 0;
function jumpRows() {
  const q = norm($("#jump-filter").value.trim());
  const words = q.split(/\s+/).filter(Boolean);
  return S.items.filter((x) => {
    const hay = norm(`${x.order} ${x.language} ${x.group} ${x.mode} ${x.employer} ${x.title} ${x.labelled ? "done" : "todo"}`);
    return words.every((w) => hay.includes(w));
  });
}
function renderJump() {
  const rows = jumpRows();
  jumpSel = Math.max(0, Math.min(jumpSel, rows.length - 1));
  $("#jump-list").innerHTML = rows.map((x, i) => `<div class="jrow${i === jumpSel ? " sel" : ""}${S.item && x.posting_id === S.item.posting_id ? " cur" : ""}" data-pid="${x.posting_id}">
    <span class="o">${x.order}</span><span class="l">${esc(x.language)}</span><span class="g">${esc(x.group)}</span>
    <span class="m ${x.mode}">${x.mode === "blind" ? "blind" : "pre-filled"}</span><span class="d">${x.labelled ? "✓" : ""}</span>
    <span class="t">${esc(x.title)} <span>· ${esc(x.employer)}</span></span></div>`).join("");
  const sel = $("#jump-list .sel");
  if (sel) sel.scrollIntoView({ block: "nearest" });
}
function openJump() {
  const dlg = $("#jump");
  $("#jump-filter").value = "";
  jumpSel = Math.max(0, S.items.findIndex((x) => S.item && x.posting_id === S.item.posting_id));
  renderJump();
  dlg.showModal();
  $("#jump-filter").focus();
}

// ---------------------------------------------------------------- keyboard

function yearsKey(key) {
  if (key === "n" || key === "N") { S.ybuf = ""; pickValue("min_years", "not_stated"); return true; }
  if (key === "+" || key === "=") { S.ybuf = ""; pickValue("min_years", "15_plus"); return true; }
  if (!/^\d$/.test(key)) return false;
  clearTimeout(S.ytimer);
  S.ybuf += key;
  const commit = () => {
    const n = Number(S.ybuf);
    S.ybuf = "";
    if (n <= 15) pickValue("min_years", String(n)); else { toast(`${n} is above 15: use + for 15_plus`); refresh(); }
  };
  // "1" may start 10-15: wait briefly for a second digit.
  if (S.ybuf === "1") { S.ytimer = setTimeout(commit, 600); refresh(); } else commit();
  return true;
}

function onKey(ev) {
  if (!S.f) return;
  const t = ev.target;
  const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA");
  if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); save(); return; }
  if (document.querySelector("dialog[open]")) return;
  if (typing) {
    if (ev.key === "Escape") { t.blur(); ev.preventDefault(); }
    return;
  }
  if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
  const name = activeName();
  const k = ev.key;
  if (name === "min_years" && yearsKey(k)) { ev.preventDefault(); return; }
  if (/^\d$/.test(k)) {
    ev.preventDefault();
    const n = Number(k);
    if (name === "office_locations") {
      if (n === 0) { toggleLoc("outside_switzerland"); return; }
      const c = locCandidates()[n - 1];
      if (c) toggleLoc(c.key);
      return;
    }
    const values = name === "relevant" ? S.spec.relevant_values : S.specBy[name].values.map((v) => v.value);
    if (n >= 1 && n <= values.length) pickValue(name, values[n - 1]);
    return;
  }
  switch (k) {
    case "j": case "J": case "ArrowDown": ev.preventDefault(); setActive(S.active + 1); break;
    case "k": case "K": case "ArrowUp": ev.preventDefault(); setActive(S.active - 1); break;
    case "Enter": ev.preventDefault(); confirmActive(); break;
    case "A": if (ev.shiftKey) { ev.preventDefault(); confirmAll(); } break;
    case "[": ev.preventDefault(); step(-1); break;
    case "]": ev.preventDefault(); step(1); break;
    case "u": case "U": {
      ev.preventDefault();
      const nx = nextUnlabelled(S.item.order);
      if (nx) goTo(nx.posting_id); else toast("Everything is labelled");
      break;
    }
    case "g": case "G": ev.preventDefault(); openJump(); break;
    case "?": ev.preventDefault(); $("#help").showModal(); break;
    case "/":
      if (name === "office_locations") { ev.preventDefault(); $("#loc-q").focus(); }
      break;
    case "e": case "E": {
      ev.preventDefault();
      const key = ev.shiftKey ? "_posting" : name;
      if (key === "relevant" || key === "office_locations") { toast("Use the posting note (Shift+E) for this field."); break; }
      S.noteOpen[key] = true;
      renderForm();
      const el = key === "_posting" ? $("#post-note") : $(`textarea[data-notefor="${key}"]`);
      if (el) el.focus();
      break;
    }
    default:
  }
}

// ---------------------------------------------------------------- wiring

function wire() {
  $("#posting").addEventListener("click", onSentenceClick);
  const form = $("#form-body");
  form.addEventListener("click", (ev) => {
    const go = ev.target.closest("[data-goto]");
    if (go) { ev.stopPropagation(); scrollToSentence(Number(go.dataset.goto)); return; }
    const fieldEl = ev.target.closest(".field");
    if (!fieldEl) {
      const nt = ev.target.closest("[data-note]");
      if (nt) { S.noteOpen[nt.dataset.note] = true; renderForm(); const el = $("#post-note"); if (el) el.focus(); }
      return;
    }
    const name = fieldEl.dataset.field;
    const idx = Number(fieldEl.dataset.idx);
    if (idx !== S.active) { setActive(idx); return; }
    const opt = ev.target.closest(".opt");
    if (opt) { S.ybuf = ""; pickValue(name, opt.dataset.value); return; }
    const loc = ev.target.closest("[data-loc]");
    if (loc) { toggleLoc(loc.dataset.loc); return; }
    const add = ev.target.closest("[data-addloc]");
    if (add) { S.locQuery = ""; toggleLoc(add.dataset.addloc); return; }
    const nt = ev.target.closest("[data-note]");
    if (nt) {
      S.noteOpen[nt.dataset.note] = true;
      renderForm();
      const el = $(`textarea[data-notefor="${nt.dataset.note}"]`);
      if (el) el.focus();
    }
  });
  form.addEventListener("input", (ev) => {
    const t = ev.target;
    if (t.dataset.notefor) S.f[t.dataset.notefor].note = t.value;
    else if (t.id === "post-note") S.f._note = t.value;
    else if (t.id === "loc-q") { S.locQuery = t.value; renderLocResults(); return; }
    renderTop();
  });
  form.addEventListener("keydown", (ev) => {
    if (ev.target.id !== "loc-q") return;
    if (ev.key === "Enter" && !ev.ctrlKey && !ev.metaKey) {
      ev.preventDefault();
      const first = locResults()[0];
      if (first) { S.locQuery = ""; toggleLoc(first); $("#loc-q").focus(); }
    }
  });
  form.addEventListener("mouseover", (ev) => {
    const f = ev.target.closest(".field");
    const name = f ? f.dataset.field : null;
    const y = ev.target.closest(".ygrid .opt");
    const yv = y ? y.dataset.value : null;
    if (yv !== S.yhover && activeName() === "min_years") {
      S.yhover = yv;
      const d = $(".ydef");
      const vs = valueSpec("min_years", yv || S.f.min_years.value);
      if (d) d.innerHTML = vs ? `<b>${esc(vs.value)}</b>: ${esc(vs.definition)}` : "";
    }
    if (name !== S.hover) { S.hover = name; updateMarks(); }
  });
  form.addEventListener("mouseleave", () => { S.hover = null; updateMarks(); });
  $("#save").addEventListener("click", save);
  $("#confirm-all").addEventListener("click", confirmAll);
  $("#prev-item").addEventListener("click", () => step(-1));
  $("#next-item").addEventListener("click", () => step(1));
  $("#jump-open").addEventListener("click", openJump);
  $("#jump-filter").addEventListener("input", () => { jumpSel = 0; renderJump(); });
  $("#jump-filter").addEventListener("keydown", (ev) => {
    const rows = jumpRows();
    if (ev.key === "ArrowDown") { ev.preventDefault(); jumpSel = Math.min(jumpSel + 1, rows.length - 1); renderJump(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); jumpSel = Math.max(jumpSel - 1, 0); renderJump(); }
    else if (ev.key === "Enter") {
      ev.preventDefault();
      const r = rows[jumpSel];
      if (r) { $("#jump").close(); goTo(r.posting_id); }
    }
  });
  $("#jump-list").addEventListener("click", (ev) => {
    const r = ev.target.closest(".jrow");
    if (r) { $("#jump").close(); goTo(Number(r.dataset.pid)); }
  });
  for (const b of document.querySelectorAll("[data-close]")) {
    b.addEventListener("click", () => b.closest("dialog").close());
  }
  document.addEventListener("keydown", onKey);
  window.addEventListener("beforeunload", (ev) => {
    if (isDirty()) { ev.preventDefault(); ev.returnValue = ""; }
  });
  window.addEventListener("hashchange", () => {
    const id = Number(location.hash.slice(1));
    if (id && S.item && id !== S.item.posting_id) goTo(id);
  });
}

async function init() {
  $("#posting").innerHTML = '<div class="loading">Loading…</div>';
  const [spec, items, progress] = await Promise.all([
    api("GET", "/api/spec"), api("GET", "/api/items"), api("GET", "/api/progress"),
  ]);
  S.spec = spec.data;
  for (const f of S.spec.fields) S.specBy[f.name] = f;
  S.items = items.data;
  S.progress = progress.data;
  wire();
  const fromHash = Number(location.hash.slice(1));
  const start = S.items.find((x) => x.posting_id === fromHash) || nextUnlabelled(-1) || S.items[0];
  if (start) await loadItem(start.posting_id);
  else $("#posting").innerHTML = '<div class="loading">The test set is empty.</div>';
}

init();
