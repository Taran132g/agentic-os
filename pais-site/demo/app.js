/* ============================================================
   PAIS · local-business dashboard — demo logic (data-driven)
   Renders any vertical from DATASETS (data.js). Approve a draft →
   it sends (simulated) and the ROI counter updates. ?biz= or the
   top-bar switcher picks the vertical.
   ============================================================ */

const TYPE = {
  reputation:   { color: "var(--blue)",  tag: "Review",      ic: "★" },
  reactivation: { color: "var(--brass)", tag: "Win-back",    ic: "↩" },
  missedcall:   { color: "var(--green)", tag: "Missed call", ic: "☎" },
  reminders:    { color: "var(--brass)", tag: "Reminder",    ic: "⏰" },
  voice:        { color: "var(--green)", tag: "AI Voice",    ic: "🎙" },
};

/* When AI-Voice mode is on, swap a missed-call item for its live-answered
   variant. Real type is kept for filtering; vtype drives the tag/color. */
function viewOf(a) {
  if (state.voiceMode && a.type === "missedcall" && a.alt) {
    return { ...a, ...a.alt, type: a.type, vtype: "voice" };
  }
  return { ...a, vtype: a.type };
}

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const money = (n) => "$" + Number(n).toLocaleString("en-US");

/* live working copy of the active dataset */
let state;
let currentFilter = "all";
let incomingTimer = null;

function pickBiz() {
  const url = new URLSearchParams(location.search).get("biz");
  const stored = localStorage.getItem("demoBiz");
  const b = (url && DATASETS[url]) ? url : (stored && DATASETS[stored]) ? stored : "trappe-tavern";
  localStorage.setItem("demoBiz", b);
  return b;
}

/* ---------------- toast + count-up ---------------- */
function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.innerHTML = msg;
  $("#toaster").appendChild(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 360); }, 3200);
}

function countUp(el, to, prefix = "$") {
  const dur = 1100, start = performance.now();
  el.classList.add("counting");
  function tick(now) {
    const p = Math.min(1, (now - start) / dur);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = prefix + Math.round(to * eased).toLocaleString("en-US");
    if (p < 1) requestAnimationFrame(tick); else el.classList.remove("counting");
  }
  requestAnimationFrame(tick);
}

/* ---------------- renderers ---------------- */
function renderChrome() {
  document.body.dataset.theme = state.theme;
  $(".brand-text strong").textContent = state.label;
  $(".brand-sub").textContent = state.sub;
  $("#dateChip").textContent = new Date().toLocaleDateString("en-US",
    { weekday: "long", month: "long", day: "numeric" });
  $("#bizSwitch").value = state._id;
  document.title = `${state.label} · PAIS Control`;
}

function renderHero() {
  $("#eyebrow").textContent = state.eyebrow;
  $("#hero-h").textContent = state.heroTitle;
  $("#heroLine").innerHTML = state.heroLine;
  countUp($("#roiNumber"), state.recovered);
  $("#roiFootStrong").textContent = state.roiFoot;
}

function approvalHTML(raw) {
  const a = viewOf(raw);
  const t = TYPE[a.vtype];
  return `
  <li class="approval" data-id="${a.id}" data-type="${a.type}" style="--type-color:${t.color}">
    <div class="approval-top">
      <span class="tag">${t.tag}</span>
      <span class="approval-who">${a.who}</span>
      <span class="approval-meta">${a.meta}</span>
    </div>
    <p class="approval-context">${a.context}</p>
    <div class="draft" data-draft>
      <span class="chan">${a.channel}</span><span data-text>${a.draft}</span>
    </div>
    <div class="approval-foot">
      <div class="btn-row">
        <button class="btn-mini btn-approve" data-act="approve">Approve &amp; send</button>
        <button class="btn-mini btn-edit" data-act="edit">Edit</button>
        <button class="btn-mini btn-skip" data-act="skip">Skip</button>
      </div>
      ${a.value ? `<span class="value-chip">${money(a.value)} at stake</span>`
                : `<span class="approval-meta">reputation</span>`}
    </div>
  </li>`;
}

function renderApprovals() {
  $("#approvals").innerHTML = state.approvals.map(approvalHTML).join("");
  syncInboxMeta();
  applyFilter(currentFilter);
}

function syncInboxMeta() {
  $("#inboxCount").textContent = `${state.approvals.length} waiting`;
  $("#inboxEmpty").hidden = state.approvals.length !== 0;
}

function renderFleet() {
  $("#fleetGrid").innerHTML = state.agents.map(g => {
    const voice = state.voiceMode && g.key === "missedcall" && g.voiceName;
    const ic = voice ? "🎙" : g.ic, ac = voice ? "var(--green)" : g.ac;
    const name = voice ? g.voiceName : g.name, last = voice ? g.voiceLast : g.last;
    const stat = voice ? g.voiceStat : g.stat, lbl = voice ? g.voiceLbl : g.lbl;
    return `
    <div class="agent" style="--ac:${ac}">
      <div class="agent-ic">${ic}</div>
      <div class="agent-body">
        <div class="agent-name"><span class="status-dot ${g.on ? "on" : "idle"}"></span>${name}</div>
        <div class="agent-last">${last}</div>
      </div>
      <div class="agent-stat">
        <b>${stat}</b><span>${lbl}</span>
        ${g.on ? `<br><button class="run-now" data-run="${g.key}">run now</button>` : ""}
      </div>
    </div>`; }).join("");
}

function renderLeaks() {
  $("#leakList").innerHTML = state.leaks.map(l => `
    <li class="leak-row">
      <div class="leak-top">
        <span class="leak-name">${l.name}</span>
        <span class="leak-val">${l.val} <small>${l.note}</small></span>
      </div>
      <div class="bar"><i data-w="${l.pct}"></i></div>
    </li>`).join("");
  requestAnimationFrame(() => $$(".bar > i").forEach(i => i.style.width = i.dataset.w + "%"));
}

function renderDigest() {
  const d = state.digest, max = Math.max(...d.hours);
  $("#digest-h").textContent = d.title;
  $("#digestSub").textContent = d.sub;
  $("#hoursChart").innerHTML = d.hours.map((h, i) =>
    `<i class="${i === d.peakIdx ? "peak" : ""}" data-h="${(h / max * 100).toFixed(0)}" style="height:4px"></i>`
  ).join("");
  requestAnimationFrame(() => $$("#hoursChart > i").forEach(b => b.style.height = b.dataset.h + "%"));
  $("#digestStats").innerHTML = d.stats.map(s =>
    `<div class="stat"><span class="stat-num">${s.num}</span><span class="stat-lbl">${s.lbl}</span></div>`
  ).join("");
}

function renderFeed() {
  $("#timeline").innerHTML = state.feed.map(f => `
    <li class="tl" style="--ac:${f.ac}">
      <span class="tl-ic">${f.ic}</span>
      <div class="tl-body">${f.body}<div class="tl-time">${f.time}</div></div>
    </li>`).join("");
}

function pushFeed(item) { state.feed.unshift(item); renderFeed(); }

/* ---------------- interactions ---------------- */
function applyFilter(f) {
  currentFilter = f;
  $$(".approval").forEach(el => {
    el.style.display = (f === "all" || el.dataset.type === f) ? "" : "none";
  });
}

function removeApproval(id, sent) {
  const el = $(`.approval[data-id="${id}"]`);
  if (!el) return;
  const a = state.approvals.find(x => x.id === id);
  el.style.maxHeight = el.scrollHeight + "px";
  requestAnimationFrame(() => el.classList.add("removing"));
  setTimeout(() => {
    state.approvals = state.approvals.filter(x => x.id !== id);
    el.remove(); syncInboxMeta();
  }, 460);

  if (sent && a) {
    if (a.value) { state.recovered += a.value; countUp($("#roiNumber"), state.recovered); }
    const t = TYPE[a.type];
    pushFeed({ ic: t.ic, ac: t.color, body: `<b>You approved</b> — ${a.who.toLowerCase()} sent`, time: "just now" });
    toast(a.value ? `Sent ✓ <b>${money(a.value)}</b> back in play` : `Sent ✓ <b>review reply</b> posted`);
  }
}

function onApprovalClick(e) {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const li = btn.closest(".approval"), id = li.dataset.id, act = btn.dataset.act;
  if (act === "approve") removeApproval(id, true);
  else if (act === "skip") { removeApproval(id, false); toast("Skipped — won't send"); }
  else if (act === "edit") {
    const draft = $("[data-draft]", li);
    if (draft.getAttribute("contenteditable") === "true") {
      draft.removeAttribute("contenteditable"); btn.textContent = "Edit"; toast("Edit saved");
    } else {
      draft.setAttribute("contenteditable", "true"); $("[data-text]", li).focus(); btn.textContent = "Done";
    }
  }
}

const RUN_OUTCOMES = {
  reputation:   "found new reviews to answer",
  reactivation: "spotted more lapsed customers to reach",
  missedcall:   "no new missed calls — all clear",
  reminders:    "re-checked tomorrow's book",
  digest:       "refreshed — trends updated",
};

function runAgent(key) {
  const g = state.agents.find(a => a.key === key);
  if (!g) return;
  toast(`<b>${g.name}</b> running on your Mac…`);
  pushFeed({ ic: g.ic, ac: g.ac, body: `<b>${g.name}</b> kicked off a manual run`, time: "just now" });
  setTimeout(() => {
    pushFeed({ ic: g.ic, ac: g.ac, body: `<b>${g.name}</b> ${RUN_OUTCOMES[key] || "finished"}`, time: "just now" });
    toast(`<b>${g.name}</b> done ✓`);
  }, 1600);
}

function simulateIncoming() {
  const base = state.incoming;
  if (!base || state.approvals.some(a => a.id === base.id)) return;
  const inc = state.voiceMode && base.alt ? { ...base, ...base.alt, type: base.type } : base;
  state.approvals.unshift({ ...base });   // keep alt available for later toggles
  renderApprovals();
  const t = TYPE[state.voiceMode && base.alt ? "voice" : base.type];
  pushFeed({ ic: t.ic, ac: t.color, body: inc.feedBody, time: "just now" });
  toast(inc.toast);
  $("#runtimePill").animate(
    [{ transform: "scale(1)" }, { transform: "scale(1.06)" }, { transform: "scale(1)" }],
    { duration: 500, easing: "ease-out" });
}

/* ---------------- load / switch ---------------- */
function load(bizId) {
  if (incomingTimer) clearTimeout(incomingTimer);
  state = structuredClone(DATASETS[bizId]);
  state._id = bizId;
  state.voiceMode = false;
  currentFilter = "all";
  $$("#voiceToggle .seg-btn").forEach(x => x.classList.toggle("is-active", x.dataset.voice === "off"));
  $$(".seg-btn").forEach(x => x.classList.toggle("is-active", x.dataset.filter === "all"));

  renderChrome();
  renderHero();
  renderApprovals();
  renderFleet();
  renderLeaks();
  renderDigest();
  renderFeed();

  incomingTimer = setTimeout(simulateIncoming, 9000);  // live drama
}

function init() {
  $("#approvals").addEventListener("click", onApprovalClick);
  $("#fleetGrid").addEventListener("click", e => {
    const r = e.target.closest("[data-run]"); if (r) runAgent(r.dataset.run);
  });
  $("#inboxFilter").addEventListener("click", e => {
    const b = e.target.closest(".seg-btn"); if (!b) return;
    $$(".seg-btn").forEach(x => x.classList.toggle("is-active", x === b));
    applyFilter(b.dataset.filter);
  });
  $("#reviewAllBtn").addEventListener("click", () =>
    $("#approvals").scrollIntoView({ behavior: "smooth", block: "center" }));
  $("#briefBtn").addEventListener("click", () =>
    toast(`🔊 <b>Brief:</b> ${state.heroLine.replace(/<[^>]+>/g, "")}`));
  $("#voiceToggle").addEventListener("click", e => {
    const b = e.target.closest(".seg-btn"); if (!b) return;
    $$("#voiceToggle .seg-btn").forEach(x => x.classList.toggle("is-active", x === b));
    state.voiceMode = b.dataset.voice === "on";
    renderApprovals();
    renderFleet();
    toast(state.voiceMode
      ? "🎙 <b>AI Voice on</b> — the receptionist answers live, calls are never missed"
      : "☎ <b>Text-back mode</b> — AI texts back after a ring-out");
  });
  $("#bizSwitch").addEventListener("change", e => {
    localStorage.setItem("demoBiz", e.target.value);
    const url = new URL(location); url.searchParams.set("biz", e.target.value);
    history.replaceState({}, "", url);
    load(e.target.value);
  });

  load(pickBiz());
}

document.addEventListener("DOMContentLoaded", init);
