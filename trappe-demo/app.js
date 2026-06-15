/* ============================================================
   PAIS · Trappe Tavern dashboard — demo logic
   Mock data stands in for the blob the on-machine runtime pushes.
   Approve a draft → it sends (simulated) and ROI updates.
   ============================================================ */

const TYPE = {
  reputation:   { color: "var(--blue)",  tag: "Review",    ic: "★" },
  reactivation: { color: "var(--brass)", tag: "Win-back",  ic: "↩" },
  missedcall:   { color: "var(--green)", tag: "Missed call", ic: "☎" },
  reminders:    { color: "var(--brass)", tag: "Reminder",  ic: "⏰" },
};

/* ---- seed state (what the runtime would POST to the website) ---- */
const state = {
  recovered: 4280,          // $ recovered this month so far
  metrics: { reviews: 11, winbacks: 7, calls: 9, noshows: 4 },
  approvals: [
    {
      id: "a1", type: "reputation", who: "Reply to Dave M.", meta: "★★☆☆☆ · 2h ago",
      context: "Dave left a <b>2-star</b> review: “Waited 25 min for a table on Friday, no one checked on us.”",
      channel: "Post as Google reply",
      draft: "Hey Dave — that's on us, Friday got away from the floor and we should've kept you in the loop. We've added a host slot for weekend rushes. Come back in and let me buy your first round — see you at the bar. — Tom, Trappe Tavern",
      value: 0,
    },
    {
      id: "a2", type: "reputation", who: "Ask 6 happy guests for a review", meta: "5★ visits · last night",
      context: "<b>6 guests</b> tipped 22%+ and stayed past close last night — prime candidates for a 5★ ask.",
      channel: "Send via email",
      draft: "Glad you closed the place down with us last night 🍻 If the wings + the Yuengling treated you right, a quick Google review helps the next neighbor find us: [review link]. Thanks for being a regular. — Trappe Tavern",
      value: 220,
    },
    {
      id: "a3", type: "missedcall", who: "Text back (610) 555-0148", meta: "missed at 9:52pm",
      context: "Call came in <b>after the host stand cleared</b>. Likely a reservation or large party.",
      channel: "Owner-send SMS",
      draft: "Hi! This is Trappe Tavern — sorry we missed you, the bar got loud 😄 Were you looking to book a table or ask about a party? Reply here and we'll get you set.",
      value: 180,
    },
    {
      id: "a4", type: "reactivation", who: "Win back 7 quiet regulars", meta: "no visit in 40+ days",
      context: "<b>7 regulars</b> who used to come weekly haven't been in for 6 weeks. Pulled from the reservation log.",
      channel: "Send via email",
      draft: "We haven't seen you at the bar in a bit and it's not the same without you. Through Sunday: your next app is on the house — just show this. Hope all's well. — The Trappe Tavern crew",
      value: 540,
    },
    {
      id: "a5", type: "reputation", who: "Reply to Karen P.", meta: "★★★★★ · 5h ago",
      context: "Karen left a <b>5-star</b> review praising the new fall menu and the bartender Mike.",
      channel: "Post as Google reply",
      draft: "Karen, this made Mike's whole week 🙌 The fall menu was his pet project so he'll be thrilled. Thanks for taking the time — see you next round. — Trappe Tavern",
      value: 0,
    },
  ],
  agents: [
    { key: "reputation",   ic: "★", ac: "var(--blue)",  name: "Reputation",  on: true,  last: "Drafted 3 replies + 6 review asks", stat: "4.3", lbl: "★ google" },
    { key: "reactivation", ic: "↩", ac: "var(--brass)", name: "Reactivation", on: true,  last: "Found 7 quiet regulars", stat: "7", lbl: "to win back" },
    { key: "missedcall",   ic: "☎", ac: "var(--green)", name: "Missed-Call", on: true,  last: "Caught 1 after-hours call", stat: "9", lbl: "saved / mo" },
    { key: "digest",       ic: "▤", ac: "var(--brass)", name: "Digest",      on: true,  last: "Compiled last night's room", stat: "11", lbl: "pm peak" },
    { key: "reminders",    ic: "⏰", ac: "var(--text-faint)", name: "Reminders", on: false, last: "Idle — needs a booking system", stat: "—", lbl: "off" },
  ],
  leaks: [
    { name: "Unanswered reviews → lost rank", pct: 82, val: "11 answered", note: "+1 star trend" },
    { name: "After-hours calls → lost parties", pct: 64, val: "9 caught", note: "≈ $1,600" },
    { name: "Regulars drifting away", pct: 48, val: "7 re-engaged", note: "≈ $540" },
    { name: "No-shows on weekend tables", pct: 30, val: "4 recovered", note: "≈ $300" },
  ],
  digest: {
    hours: [2,3,4,6,9,14,22,30,41,52,68,90,76,40], // 11am→12am
    peakIdx: 11, avgTicket: 34, peakHour: "11pm", topItem: "Wings",
  },
  feed: [
    { ic: "★", ac: "var(--blue)",  body: "<b>Reputation</b> drafted a reply to Dave M.'s 2★ review", time: "2h ago" },
    { ic: "☎", ac: "var(--green)", body: "<b>Missed-Call</b> caught a call at 9:52pm after the host left", time: "2h ago" },
    { ic: "▤", ac: "var(--brass)", body: "<b>Digest</b> flagged 11pm as last night's peak — staff the bar", time: "6h ago" },
    { ic: "↩", ac: "var(--brass)", body: "<b>Reactivation</b> found 7 regulars quiet for 40+ days", time: "8h ago" },
    { ic: "★", ac: "var(--blue)",  body: "<b>Reputation</b> queued 6 review asks from last night's happy tables", time: "9h ago" },
  ],
};

/* ---------------- helpers ---------------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const money = (n) => "$" + n.toLocaleString("en-US");

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = msg;
  $("#toaster").appendChild(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 360); }, 3200);
}

function countUp(el, to, prefix = "$") {
  const dur = 1100, start = performance.now();
  const from = 0;
  el.classList.add("counting");
  function tick(now) {
    const p = Math.min(1, (now - start) / dur);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = prefix + Math.round(from + (to - from) * eased).toLocaleString("en-US");
    if (p < 1) requestAnimationFrame(tick);
    else el.classList.remove("counting");
  }
  requestAnimationFrame(tick);
}

/* ---------------- renderers ---------------- */
function renderDate() {
  const d = new Date();
  $("#dateChip").textContent = d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}

function renderHero() {
  const a = state.approvals.length;
  $("#heroLine").innerHTML =
    `They caught <b>1 missed call</b>, drafted <b>${state.metrics.reviews} review replies</b>, and lined up <b>${state.metrics.winbacks} regulars</b> to win back. ` +
    `<b>${a} drafts</b> are waiting for your OK below.`;
  countUp($("#roiNumber"), state.recovered);
  $("#roiFootStrong").textContent = `+${money(960)} this week`;
}

function approvalHTML(a) {
  const t = TYPE[a.type];
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
      ${a.value ? `<span class="value-chip">${money(a.value)} at stake</span>` : `<span class="approval-meta">reputation</span>`}
    </div>
  </li>`;
}

function renderApprovals() {
  $("#approvals").innerHTML = state.approvals.map(approvalHTML).join("");
  syncInboxMeta();
  applyFilter(currentFilter);
}

function syncInboxMeta() {
  const n = state.approvals.length;
  $("#inboxCount").textContent = `${n} waiting`;
  $("#inboxEmpty").hidden = n !== 0;
}

function renderFleet() {
  $("#fleetGrid").innerHTML = state.agents.map(g => `
    <div class="agent" style="--ac:${g.ac}">
      <div class="agent-ic">${g.ic}</div>
      <div class="agent-body">
        <div class="agent-name">
          <span class="status-dot ${g.on ? "on" : "idle"}"></span>${g.name}
        </div>
        <div class="agent-last">${g.last}</div>
      </div>
      <div class="agent-stat">
        <b>${g.stat}</b><span>${g.lbl}</span>
        ${g.on ? `<br><button class="run-now" data-run="${g.key}">run now</button>` : ""}
      </div>
    </div>`).join("");
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
  $("#hoursChart").innerHTML = d.hours.map((h, i) =>
    `<i class="${i === d.peakIdx ? "peak" : ""}" data-h="${(h / max * 100).toFixed(0)}" style="height:4px" title="${11 + i > 12 ? (11 + i) % 12 : 11 + i}${11 + i >= 12 ? "pm" : "am"}"></i>`
  ).join("");
  requestAnimationFrame(() => $$("#hoursChart > i").forEach(b => b.style.height = b.dataset.h + "%"));
  $("#avgTicket").textContent = money(d.avgTicket);
  $("#peakHour").textContent = d.peakHour;
  $("#topItem").textContent = d.topItem;
}

function renderFeed() {
  $("#timeline").innerHTML = state.feed.map(f => `
    <li class="tl" style="--ac:${f.ac}">
      <span class="tl-ic">${f.ic}</span>
      <div class="tl-body">${f.body}<div class="tl-time">${f.time}</div></div>
    </li>`).join("");
}

function pushFeed(item) {
  state.feed.unshift(item);
  renderFeed();
}

/* ---------------- interactions ---------------- */
let currentFilter = "all";

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
    el.remove();
    syncInboxMeta();
  }, 460);

  if (sent && a) {
    if (a.value) {
      state.recovered += a.value;
      countUp($("#roiNumber"), state.recovered);
    }
    const t = TYPE[a.type];
    pushFeed({ ic: t.ic, ac: t.color, body: `<b>You approved</b> — ${a.who.toLowerCase()} sent`, time: "just now" });
    toast(a.value ? `Sent ✓ <b>${money(a.value)}</b> back in play` : `Sent ✓ <b>review reply</b> posted`);
  }
}

function onApprovalClick(e) {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const li = btn.closest(".approval");
  const id = li.dataset.id;
  const act = btn.dataset.act;

  if (act === "approve") removeApproval(id, true);
  else if (act === "skip") { removeApproval(id, false); toast("Skipped — won't send"); }
  else if (act === "edit") {
    const txt = $("[data-text]", li);
    const draft = $("[data-draft]", li);
    if (draft.getAttribute("contenteditable") === "true") {
      draft.removeAttribute("contenteditable");
      btn.textContent = "Edit";
      toast("Edit saved");
    } else {
      draft.setAttribute("contenteditable", "true");
      txt.focus();
      btn.textContent = "Done";
    }
  }
}

function runAgent(key) {
  const g = state.agents.find(a => a.key === key);
  if (!g) return;
  toast(`<b>${g.name}</b> running on your Mac…`);
  pushFeed({ ic: g.ic, ac: g.ac, body: `<b>${g.name}</b> kicked off a manual run`, time: "just now" });
  setTimeout(() => {
    const outcomes = {
      reputation:   "found 2 new reviews to answer",
      reactivation: "spotted 3 more first-timers who never returned",
      missedcall:   "no new missed calls — all clear",
      digest:       "refreshed — Friday is trending +18%",
    };
    pushFeed({ ic: g.ic, ac: g.ac, body: `<b>${g.name}</b> ${outcomes[key] || "finished"}`, time: "just now" });
    toast(`<b>${g.name}</b> done ✓`);
  }, 1600);
}

/* simulate a live missed call landing in the inbox */
function simulateIncoming() {
  const newCall = {
    id: "live1", type: "missedcall", who: "Text back (484) 555-0193", meta: "missed just now",
    context: "<b>Live:</b> call came in and rang out — the floor's slammed right now.",
    channel: "Owner-send SMS",
    draft: "Hey! Trappe Tavern here — sorry we couldn't grab the phone, we're packed 🙌 Can we book you a table or answer a quick question? Just reply here.",
    value: 150,
  };
  state.approvals.unshift(newCall);
  renderApprovals();
  const t = TYPE.missedcall;
  pushFeed({ ic: t.ic, ac: t.color, body: `<b>Missed-Call</b> caught a live call — draft ready to send`, time: "just now" });
  toast(`☎ <b>New missed call</b> — text-back drafted`);
  const pill = $("#runtimePill");
  pill.animate([{ transform: "scale(1)" }, { transform: "scale(1.06)" }, { transform: "scale(1)" }], { duration: 500, easing: "ease-out" });
}

/* ---------------- wire-up ---------------- */
function init() {
  renderDate();
  renderHero();
  renderApprovals();
  renderFleet();
  renderLeaks();
  renderDigest();
  renderFeed();

  $("#approvals").addEventListener("click", onApprovalClick);
  $("#fleetGrid").addEventListener("click", e => {
    const r = e.target.closest("[data-run]");
    if (r) runAgent(r.dataset.run);
  });
  $("#inboxFilter").addEventListener("click", e => {
    const b = e.target.closest(".seg-btn");
    if (!b) return;
    $$(".seg-btn").forEach(x => x.classList.toggle("is-active", x === b));
    applyFilter(b.dataset.filter);
  });
  $("#reviewAllBtn").addEventListener("click", () => {
    $("#approvals").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $("#briefBtn").addEventListener("click", () => {
    toast("🔊 <b>Morning brief:</b> 11 reviews handled, 7 regulars to win back, 1 missed call caught. Recovered " + money(state.recovered) + " this month.");
  });

  // live drama after 9s
  setTimeout(simulateIncoming, 9000);
}

document.addEventListener("DOMContentLoaded", init);
