/* jobfacts labelling page. Plain JS, no build step. Talks only to the JSON API in app.py
   (documented in docs/labelling.md). */
"use strict";

// ---------------------------------------------------------------- constants

const ORDER = [
  "relevant", "work_mode", "remote_from_switzerland", "office_cantons",
  "german", "french", "min_years", "title_level",
];
const LABEL = {
  relevant: "Relevant", work_mode: "Work mode", remote_from_switzerland: "Remote from CH",
  office_cantons: "Office locations", german: "German", french: "French",
  min_years: "Min. years", title_level: "Title level",
};
const ABBR = {
  work_mode: "MODE", remote_from_switzerland: "RCH", office_cantons: "LOC",
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
  office_cantons: "Offices for this role only (not history, customers or travel); a town counts for its canton. Nothing selected = not stated.",
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
  locQuery: "",        // canton filter text (office field)
  locSel: 0,           // index of the highlighted match in the filter
  cantonBy: {},        // canton code -> /api/spec canton
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
  f.office_cantons.cantons = new Set();
  f.office_cantons.places = new Set();
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
    // Labels saved before the canton-first fields have no office_cantons: leave that field open.
    if (Array.isArray(label.office_cantons)) {
      const o = f.office_cantons;
      o.cantons = new Set(label.office_cantons);
      o.places = new Set(label.office_places || []);
      o.sentences = new Set(label.office_cantons_sentences || []);
      o.status = "set";
    }
    f._note = label.note || "";
  } else if (item.prefill) {
    for (const [name, e] of Object.entries(item.prefill.fields || {})) {
      if (!f[name]) continue;
      Object.assign(f[name], { value: e.value, sentences: new Set(e.sentences || []), status: "suggested" });
    }
    if (Array.isArray(item.prefill.office_cantons)) {
      const o = f.office_cantons;
      o.cantons = new Set(item.prefill.office_cantons);
      o.places = new Set(item.prefill.office_places || []);
      o.sentences = new Set(item.prefill.office_cantons_sentences || []);
      o.status = "suggested";
    }
  }
  return f;
}

function serialize(f) {
  const out = { note: f._note };
  for (const name of ORDER) {
    const x = f[name];
    out[name] = [x.value, [...x.sentences].sort((a, b) => a - b), x.note, x.status,
      x.cantons ? [...x.cantons].sort() : null, x.places ? [...x.places].sort() : null];
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
  if (name === "office_cantons") return S.f.office_cantons.cantons.size > 0;
  const v = S.f[name].value;
  if (v === null) return true; // evidence may be marked before the value is picked
  const vs = valueSpec(name, v);
  return vs ? vs.needs_sentence : false;
}
function needsSentence(name) {
  if (name === "relevant") return false;
  if (name === "office_cantons") return S.f.office_cantons.cantons.size > 0;
  const vs = valueSpec(name, S.f[name].value);
  return vs ? vs.needs_sentence : false;
}

// Client-side check of one field; returns an error message or null.
function checkField(name) {
  const x = S.f[name];
  if (name !== "office_cantons" && x.value === null) return "Pick a value.";
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

// The posting has two views. "doc" (default) rebuilds paragraphs, headings and lists from the
// text between sentences; "lines" shows one numbered row per sentence. Both make one clickable
// `.sent` element per sentence id, so evidence, marks and scrolling work the same way.
const VIEW_KEY = "jobfacts.label.view";
function loadView() {
  try { return localStorage.getItem(VIEW_KEY) === "lines" ? "lines" : "doc"; } catch { return "doc"; }
}
function saveView(v) {
  try { localStorage.setItem(VIEW_KEY, v); } catch { /* storage blocked: keep it for this page only */ }
}
S.view = loadView();
const docAvailable = () => Boolean(S.item && Array.isArray(S.item.offsets));
const currentView = () => (S.view === "doc" && docAvailable() ? "doc" : "lines");

function toggleView() {
  if (!S.item) return;
  if (!docAvailable()) { toast("Document view is not available for this posting"); return; }
  // Keep the reader's place: the first sentence visible now is scrolled back into view.
  let anchor = null;
  for (const [id, el] of S.sentEls) {
    if (id > 0 && el.offsetParent && el.getBoundingClientRect().top >= 56) { anchor = id; break; }
  }
  S.view = S.view === "doc" ? "lines" : "doc";
  saveView(S.view);
  renderPosting();
  updateMarks();
  const el = anchor !== null && S.sentEls.get(anchor);
  if (el && el.offsetParent) el.scrollIntoView({ block: "start" });
  window.scrollBy(0, -70);
}

function candidateRx(it) {
  const matched = [...new Set(Object.values(it.place_candidates || {}).flat().map((c) => c.matched).filter(Boolean))]
    .sort((a, b) => b.length - a.length)
    .map((m) => m.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return matched.length ? rx(matched.join("|")) : null;
}

function renderPosting() {
  const it = S.item;
  const pane = $("#posting");
  const candRx = candidateRx(it);
  const view = currentView();
  const inner = document.createElement("div");
  inner.className = `posting-inner view-${view}`;
  const toggleTitle = docAvailable() ? "Switch view (Shift+V)" : "Document view is not available for this posting";
  inner.innerHTML = `<div class="employer"><span>${esc(it.employer)} · posting ${it.posting_id}</span>
    <span class="view-toggle" role="group" aria-label="View" title="${toggleTitle}">
      <button type="button" data-view="doc" class="${view === "doc" ? "on" : ""}"${docAvailable() ? "" : " disabled"}>Document</button>
      <button type="button" data-view="lines" class="${view === "lines" ? "on" : ""}">Lines</button>
    </span></div>`;
  S.sentEls = new Map();
  const meta = [
    { id: TITLE_ID, text: it.title, cls: "meta title" },
    { id: LOCATION_ID, text: it.location_raw || "", cls: "meta loc meta-last" },
  ];
  const rows = view === "lines" ? [...meta, ...it.sentences.map((s) => ({ id: s.id, text: s.text, cls: "" }))] : meta;
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
  if (view === "doc") inner.append(renderDoc(it, candRx));
  pane.replaceChildren(inner);
}

// ---------------------------------------------------------------- document view

const MARKER_RX = /^(?:[-*+•·▪◦‣–—]|\d{1,2}[.)])$/;        // a bullet marker on its own
const LEAD_MARKER_RX = /^\s*(?:([-*+•·▪◦‣–—])|(\d{1,2})[.)])\s/; // marker at the start of a line
const NEWLINE_RX = /\r\n|[\n\r\u2028\u2029]/;

// Group the sentences into source lines. `head` is the text on the line before the first
// sentence (a bullet marker or nothing), `blank` says a blank line came before the line.
function sourceLines(text, sents) {
  const lines = [];
  let pos = 0;
  let cur = null;
  for (const s of sents) {
    const parts = text.slice(pos, s.start).split(NEWLINE_RX);
    pos = s.end;
    if (cur && parts.length === 1) { cur.sents.push(s); continue; }
    const middle = cur ? parts.slice(1, -1) : parts.slice(0, -1);
    cur = {
      sents: [s],
      head: parts[parts.length - 1],
      blank: middle.some((p) => !p.trim()) || lines.length === 0,
    };
    lines.push(cur);
  }
  return lines;
}

// One block per heading, list or paragraph. Rules:
//   - a line that starts with a bullet marker (or follows a line holding only a marker) is a
//     list item; consecutive items form one list, even with blank lines between them;
//   - a short standalone line without ending punctuation is a heading;
//   - other lines are paragraphs; a line right after another paragraph line (no blank line)
//     continues that paragraph after a line break.
function docBlocks(text, sents) {
  const lines = sourceLines(text, sents);
  const blocks = [];
  let carry = null; // a "-" sentence alone on its line: the next line is its list item
  for (const ln of lines) {
    if (ln.sents.length === 1 && MARKER_RX.test(ln.sents[0].text.trim())) {
      carry = { sent: ln.sents[0], blank: ln.blank || (carry ? carry.blank : false),
                ordered: /\d/.test(ln.sents[0].text) };
      continue;
    }
    const lead = ln.head.match(LEAD_MARKER_RX);
    const hidden = carry ? [carry.sent] : [];
    const blank = ln.blank || Boolean(carry && carry.blank);
    let kind;
    let ordered = false;
    if (carry || lead) {
      kind = "li";
      ordered = carry ? carry.ordered : Boolean(lead[2]);
    } else if (isHeading(ln.sents)) {
      kind = "h";
    } else {
      kind = "p";
    }
    carry = null;
    const last = blocks[blocks.length - 1];
    if (kind === "li") {
      const item = { hidden, sents: ln.sents };
      if (last && last.kind === "list" && last.ordered === ordered) last.items.push(item);
      else blocks.push({ kind: "list", ordered, items: [item] });
    } else if (kind === "p" && last && last.kind === "p" && !blank) {
      last.lines.push(ln.sents);
    } else if (kind === "p") {
      blocks.push({ kind: "p", lines: [ln.sents] });
    } else {
      blocks.push({ kind: "h", sents: ln.sents });
    }
  }
  if (carry) blocks.push({ kind: "p", lines: [[carry.sent]] });
  return blocks;
}

function isHeading(sents) {
  if (sents.length !== 1) return false;
  const t = sents[0].text.trim();
  if (t.length > 70 || !/\p{L}/u.test(t) || /^#\S+$/.test(t)) return false; // not "#Hashtag"
  const words = t.split(/\s+/).length;
  if (words > 10) return false;
  if (/:["'”’)\]]*$/.test(t)) return words <= 6;
  return !/[.!?…;,]["'”’)\]]*$/.test(t);
}

function renderDoc(it, candRx) {
  const text = it.description_text;
  const sents = it.sentences.map((s, i) => ({ id: s.id, text: s.text, start: it.offsets[i][0], end: it.offsets[i][1] }));
  const doc = document.createElement("article");
  doc.className = "doc";
  const sentEl = (s, hidden = false) => {
    const el = document.createElement("span");
    el.className = "sent ds" + (hidden ? " marker-only" : "");
    el.dataset.id = s.id;
    el.innerHTML = `<sup class="dnum">${s.id}</sup><span class="txt">${cueHtml(s.text, s.id, candRx)}</span><span class="marks"></span>`;
    S.sentEls.set(s.id, el);
    return el;
  };
  const fill = (parent, list) => list.forEach((s, i) => {
    if (i) parent.append(" ");
    parent.append(sentEl(s));
  });
  for (const b of docBlocks(text, sents)) {
    if (b.kind === "h") {
      const h = document.createElement("h3");
      fill(h, b.sents);
      doc.append(h);
    } else if (b.kind === "p") {
      const p = document.createElement("p");
      b.lines.forEach((line, i) => {
        if (i) p.append(document.createElement("br"));
        fill(p, line);
      });
      doc.append(p);
    } else {
      const list = document.createElement(b.ordered ? "ol" : "ul");
      for (const item of b.items) {
        const li = document.createElement("li");
        for (const s of item.hidden) li.append(sentEl(s, true));
        fill(li, item.sents);
        list.append(li);
      }
      doc.append(list);
    }
  }
  return doc;
}

function updateMarks() {
  const act = activeName();
  document.body.dataset.active = act;
  for (const [id, el] of S.sentEls) {
    el.classList.remove("ev-set", "ev-sugg", "ev-hover", "marked");
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
    if (marks.length) el.classList.add("marked");
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
  const viewBtn = ev.target.closest("[data-view]");
  if (viewBtn) {
    viewBtn.blur();
    if (!viewBtn.disabled && viewBtn.dataset.view !== currentView()) toggleView();
    return;
  }
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
    if (name === "office_cantons") toast("Select a canton first, then mark its sentence.");
    else toast(`${x.value} takes no sentences. Pick another value first.`);
    return;
  }
  if (x.sentences.has(id)) x.sentences.delete(id); else x.sentences.add(id);
  if (x.status === "suggested" || (x.status === "empty" && x.value !== null)) x.status = "set";
  delete S.errors[name];
  refresh();
}

// ---------------------------------------------------------------- form pane

const norm = (s) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();

function evLinks(x) {
  const ids = [...x.sentences].sort((a, b) => a - b);
  if (!ids.length) return "";
  return "ev " + ids.map((i) => `<a data-goto="${i}">${sid(i)}</a>`).join(", ");
}

function headValue(name) {
  const x = S.f[name];
  if (name === "office_cantons") return officeHead();
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

// ---------------------------------------------------------------- office locations (cantons, then places)

// Language used for a canton's name on its toggle: the name people there use.
const CANTON_LANG = { FR: "fr", GE: "fr", JU: "fr", NE: "fr", VD: "fr", VS: "fr", TI: "it" };
// Short forms where the full name does not fit a grid cell (full names are in the tooltip).
const SHORT_NAME = { AI: "Appenzell I.Rh.", AR: "Appenzell A.Rh.", BL: "Basel-Land" };
const localName = (c) => SHORT_NAME[c.code] || c[`name_${CANTON_LANG[c.code] || "de"}`] || c.name_en;
// Grid order: alphabetical by code, so a code you know is found by its first letter.
const gridCodes = () => S.spec.cantons.map((c) => c.code).sort();
const OUTSIDE = () => S.spec.outside_value;

function placeCands(code) {
  const seen = new Set();
  return ((S.item.place_candidates || {})[code] || []).filter((c) => !seen.has(c.key) && seen.add(c.key));
}
// Canton of a place key: from the candidates, else by name in the spec (labels saved earlier).
function placeCanton(key) {
  for (const [code, cands] of Object.entries(S.item.place_candidates || {})) {
    if (cands.some((c) => c.key === key)) return code;
  }
  const name = key.split(":").slice(2).join(":");
  const c = S.spec.cantons.find((x) => x.places.includes(name));
  return c ? c.code : null;
}
function placeName(key) {
  for (const cands of Object.values(S.item.place_candidates || {})) {
    const c = cands.find((x) => x.key === key);
    if (c) return c.name;
  }
  return key.split(":").slice(2).join(":");
}
// Place rows shown under the grid, in grid order; numbered 1–9 for the keyboard.
function placeRows() {
  const o = S.f.office_cantons;
  const rows = [];
  for (const code of gridCodes()) {
    if (!o.cantons.has(code)) continue;
    const cands = placeCands(code);
    const extra = [...o.places].filter((k) => !cands.some((c) => c.key === k) && placeCanton(k) === code)
      .map((k) => ({ key: k, name: placeName(k), commune: "", matched: "", where: "saved label" }));
    rows.push({ code, places: [...cands, ...extra] });
  }
  return rows;
}
const numberedPlaces = () => placeRows().flatMap((r) => r.places).slice(0, 9);

// Filter matches for the typed text: canton code, canton name (any language), or a place name
// (the spec's main places plus this posting's candidates). Best match first.
function cantonMatches(query) {
  const q = norm(query.trim());
  if (!q) return [];
  const hints = new Set(S.item.canton_hints || []);
  const out = [];
  for (const c of S.spec.cantons) {
    const names = [c.name_en, c.name_de, c.name_fr, c.name_it].map(norm);
    const cands = placeCands(c.code);
    const places = [...new Set([...cands.map((p) => p.name), ...cands.map((p) => p.commune), ...c.places].filter(Boolean))];
    let score = 9, via = null;
    if (norm(c.code) === q) score = 0;
    else if (names.some((n) => n.startsWith(q))) score = 1;
    else {
      const p = places.find((n) => norm(n).startsWith(q));
      if (p) { score = 2; via = p; }
      else if (q.length >= 3 && names.some((n) => n.includes(q))) score = 3;
      else if (q.length >= 3) {
        const p2 = places.find((n) => norm(n).includes(q));
        if (p2) { score = 4; via = p2; }
      }
    }
    if (score < 9) {
      const cand = via ? cands.find((p) => p.name === via || p.commune === via) : null;
      out.push({ code: c.code, score, via, placeKey: cand ? cand.key : null, hint: hints.has(c.code) });
    }
  }
  const outside = norm("outside switzerland abroad ausland etranger");
  if (q.length >= 3 && outside.split(" ").some((w) => w.startsWith(q))) {
    out.push({ code: OUTSIDE(), score: 1, via: null, placeKey: null, hint: false });
  }
  return out.sort((a, b) => a.score - b.score || b.hint - a.hint || a.code.localeCompare(b.code));
}

function touchOffice() {
  const o = S.f.office_cantons;
  o.status = "set";
  if (!o.cantons.size) o.sentences.clear();
  delete S.errors.office_cantons;
}

// Select or clear a canton. Selecting pre-selects the places found in the location field or the
// title (and the place typed in the filter); clearing it drops its places.
function toggleCanton(code, placeKey = null) {
  const o = S.f.office_cantons;
  if (o.cantons.has(code)) {
    o.cantons.delete(code);
    for (const k of [...o.places]) if (placeCanton(k) === code) o.places.delete(k);
  } else {
    o.cantons.add(code);
    for (const c of placeCands(code)) if (c.where !== "posting text") o.places.add(c.key);
    if (placeKey) o.places.add(placeKey);
  }
  touchOffice();
  refresh();
}

function togglePlace(key) {
  const o = S.f.office_cantons;
  if (o.places.has(key)) o.places.delete(key); else o.places.add(key);
  touchOffice();
  refresh();
}

function officeHead() {
  const o = S.f.office_cantons;
  const cls = o.status === "suggested" ? "sugg" : "";
  if (o.status === "empty" && !o.cantons.size) return { text: "not set", cls: "empty" };
  if (!o.cantons.size) return { text: "not stated", cls };
  const codes = [...gridCodes(), OUTSIDE()].filter((c) => o.cantons.has(c))
    .map((c) => (c === OUTSIDE() ? "outside CH" : c));
  const places = [...o.places].map(placeName);
  return { text: codes.join(", ") + (places.length ? ` · ${places.join(", ")}` : ""), cls };
}

function officeHtml() {
  const o = S.f.office_cantons;
  const sugg = o.status === "suggested";
  const hints = new Set(S.item.canton_hints || []);
  const matches = cantonMatches(S.locQuery);
  S.locSel = Math.max(0, Math.min(S.locSel, matches.length - 1));
  const matchSet = new Set(matches.map((m) => m.code));
  const top = matches[S.locSel];
  const filtering = S.locQuery.trim() !== "";
  const cls = (code) => {
    const on = o.cantons.has(code);
    return ["ct", on ? "on" : "", on && sugg ? "sugg" : "", hints.has(code) ? "hint" : "",
      filtering && !matchSet.has(code) ? "dim" : "", top && top.code === code ? "best" : ""].filter(Boolean).join(" ");
  };

  let html = `<div class="cfilter"><input id="canton-q" type="search" autocomplete="off" spellcheck="false"
    placeholder="Type a code or a place: vd, lau…   Enter toggles" value="${esc(S.locQuery)}">`;
  if (filtering) {
    html += top
      ? `<div class="cmatch"><kbd>Enter</kbd> ${o.cantons.has(top.code) ? "clears" : "selects"} <b>${esc(top.code === OUTSIDE() ? "Outside Switzerland" : top.code + " " + localName(S.cantonBy[top.code]))}</b>${top.via ? ` · matched “${esc(top.via)}”` : ""}${matches.length > 1 ? ` <span class="muted">· ↑↓ ${matches.length} matches</span>` : ""}</div>`
      : '<div class="cmatch muted">No canton or place matches.</div>';
  }
  html += "</div>";

  html += `<div class="cgrid">${gridCodes().map((code) => {
    const c = S.cantonBy[code];
    const title = `${c.name_en} · ${c.name_de} / ${c.name_fr} / ${c.name_it}${hints.has(code) ? " · found in the posting" : ""}`;
    return `<button class="${cls(code)}" data-canton="${code}" title="${esc(title)}"><span class="cc">${code}</span><span class="cn">${esc(localName(c))}</span></button>`;
  }).join("")}</div>`;

  html += `<div class="crow"><button class="${cls(OUTSIDE())} out" data-canton="${OUTSIDE()}"><span class="cc">0</span><span class="cn">Outside Switzerland</span></button>`;
  html += o.cantons.size
    ? `<span class="cstate">${o.cantons.size} selected</span>`
    : '<span class="cstate none">None selected → <b>not stated</b></span>';
  html += "</div>";
  if (hints.size) html += '<div class="ckey"><span class="hint-dot"></span>found in the posting by code (a hint, not a suggestion)</div>';

  const rows = placeRows();
  if (rows.length) {
    let n = 0;
    html += '<div class="loc-sub">Places</div>';
    for (const r of rows) {
      html += `<div class="prow"><span class="pc">${r.code}</span><div class="chips">`;
      html += r.places.length ? r.places.map((p) => {
        n++;
        const on = o.places.has(p.key);
        const where = p.where === "saved label" ? p.where : `“${p.matched}” · ${p.where}`;
        return `<button class="chip${on ? " on" : ""}${on && sugg ? " sugg" : ""}" data-place="${esc(p.key)}">
          ${n <= 9 ? `<span class="k">${n}</span>` : ""}<span>${on ? "✓ " : ""}${esc(p.name)}</span><span class="where">${esc(where)}</span></button>`;
      }).join("") : '<span class="pnone">no place found in the posting</span>';
      html += "</div></div>";
    }
    html += '<div class="phint">Only places code found in the posting can be picked. Places from the location field or title start selected; check the ones from the posting text.</div>';
  }
  return html;
}

function noteHtml(name) {
  if (name === "relevant" || name === "office_cantons") return "";
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
      const q = name === "relevant" ? "" : name === "office_cantons" ? "Which cantons have an office or work location for this role?" : S.specBy[name].question;
      html += q ? `<div class="f-q">${esc(q)}</div>` : "";
      html += `<div class="f-hint">${esc(HINTS[name])}</div>`;
      html += name === "office_cantons" ? officeHtml() : optionsHtml(name);
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
  S.locSel = 0;
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

function confirmActive() {
  const name = activeName();
  const x = S.f[name];
  if (name === "office_cantons" && x.status === "empty") x.status = "set"; // empty set = not stated
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
    if (name === "relevant" || name === "office_cantons") continue;
    const x = S.f[name];
    fields[name] = {
      value: x.value,
      sentences: needsSentence(name) ? [...x.sentences].sort((a, b) => a - b) : [],
      note: x.note,
    };
  }
  const o = S.f.office_cantons;
  const codes = [...S.spec.cantons.map((c) => c.code), S.spec.outside_value];
  return {
    relevant: S.f.relevant.value,
    fields,
    office_cantons: codes.filter((c) => o.cantons.has(c)),
    office_cantons_sentences: o.cantons.size ? [...o.sentences].sort((a, b) => a - b) : [],
    // Always explicit: when omitted the server fills in the places code finds.
    office_places: [...o.places].filter((k) => { const c = placeCanton(k); return !c || o.cantons.has(c); }).sort(),
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
      const name = k.startsWith("office_") ? "office_cantons" : k;
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

// Office field: a plain letter starts the canton filter (J/K still move between fields while the
// filter is empty; press / first to type a word that starts with j or k).
function officeLetter(ev) {
  const k = ev.key;
  if (k.length !== 1 || !/\p{L}/u.test(k) || ev.shiftKey) return false;
  if ((k === "j" || k === "k") && !S.locQuery) return false;
  ev.preventDefault();
  const input = $("#canton-q");
  if (!input) return false;
  input.focus();
  S.locQuery += k;
  S.locSel = 0;
  renderForm();
  return true;
}

// Keys inside the canton filter box.
function filterKey(ev) {
  const input = ev.target;
  const k = ev.key;
  if (k === "Enter" && !ev.ctrlKey && !ev.metaKey) {
    ev.preventDefault();
    ev.stopPropagation();
    const matches = cantonMatches(S.locQuery);
    input.blur();
    if (!S.locQuery.trim()) { S.locQuery = ""; confirmActive(); return; }
    const top = matches[S.locSel];
    S.locQuery = "";
    S.locSel = 0;
    if (!top) { refresh(); return; }
    toggleCanton(top.code, S.f.office_cantons.cantons.has(top.code) ? null : top.placeKey);
  } else if (k === "Escape") {
    ev.preventDefault();
    ev.stopPropagation();
    S.locQuery = "";
    input.blur();
    refresh();
  } else if (k === "ArrowDown" || k === "ArrowUp") {
    ev.preventDefault();
    ev.stopPropagation();
    if (S.locQuery.trim()) {
      const n = cantonMatches(S.locQuery).length;
      if (n) S.locSel = (S.locSel + (k === "ArrowDown" ? 1 : n - 1)) % n;
      renderForm();
    } else {
      input.blur();
      setActive(S.active + (k === "ArrowDown" ? 1 : -1));
    }
  }
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
  if (name === "office_cantons" && officeLetter(ev)) return;
  if (/^\d$/.test(k)) {
    ev.preventDefault();
    const n = Number(k);
    if (name === "office_cantons") {
      if (n === 0) { toggleCanton(OUTSIDE()); return; }
      const p = numberedPlaces()[n - 1];
      if (p) togglePlace(p.key);
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
    case "V": if (ev.shiftKey) { ev.preventDefault(); toggleView(); } break;
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
      if (name === "office_cantons") { ev.preventDefault(); $("#canton-q").focus(); }
      break;
    case "e": case "E": {
      ev.preventDefault();
      const key = ev.shiftKey ? "_posting" : name;
      if (key === "relevant" || key === "office_cantons") { toast("Use the posting note (Shift+E) for this field."); break; }
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
    const ct = ev.target.closest("[data-canton]");
    if (ct) { S.locQuery = ""; toggleCanton(ct.dataset.canton); return; }
    const pl = ev.target.closest("[data-place]");
    if (pl) { togglePlace(pl.dataset.place); return; }
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
    else if (t.id === "canton-q") { S.locQuery = t.value; S.locSel = 0; renderForm(); return; }
    renderTop();
  });
  form.addEventListener("keydown", (ev) => {
    if (ev.target.id !== "canton-q") return;
    filterKey(ev);
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
  for (const c of S.spec.cantons) S.cantonBy[c.code] = c;
  S.items = items.data;
  S.progress = progress.data;
  wire();
  const fromHash = Number(location.hash.slice(1));
  const start = S.items.find((x) => x.posting_id === fromHash) || nextUnlabelled(-1) || S.items[0];
  if (start) await loadItem(start.posting_id);
  else $("#posting").innerHTML = '<div class="loading">The test set is empty.</div>';
}

init();
