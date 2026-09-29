/* Reputation queue — the Reputation agent's overnight drafts: owner-voiced
   replies to new Google reviews + short asks to last night's happy guests.
   Same approve / edit / skip flow as the win-back queue. */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

let R, postedReplies = 0, sentAsks = 0;

function pickBiz() {
  const url = new URLSearchParams(location.search).get("biz");
  const stored = localStorage.getItem("demoBiz");
  const b = (url && REPUTATION[url]) ? url : (stored && REPUTATION[stored]) ? stored : "trappe-tavern";
  localStorage.setItem("demoBiz", b);
  return b;
}

function stars(n) {
  return `<span class="rc-stars" aria-label="${n} out of 5 stars">${"★".repeat(n)}${"☆".repeat(5 - n)}</span>`;
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.innerHTML = msg;
  $("#toaster").appendChild(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 360); }, 2600);
}

/* Build the unified queue: replies first (most urgent), then asks. */
function buildItems(r) {
  const replies = (r.replies || []).map(x => ({ ...x, type: "reply" }));
  const asks = (r.asks || []).map(x => ({ ...x, type: "ask" }));
  return [...replies, ...asks];
}

function replyCard(c, i) {
  const low = c.stars <= 3;
  return `
  <li class="react-card rep-card ${low ? "is-low" : ""}" data-i="${i}" data-type="reply">
    <div class="rc-top">
      <span class="rc-av">${stars(c.stars)}</span>
      <div class="rc-id">
        <span class="rc-name">${c.author}</span>
        <span class="rc-meta">Google review · ${c.when}</span>
      </div>
      <span class="rep-kind ${low ? "warn" : ""}">${low ? "Needs a reply" : "Thank-you"}</span>
    </div>
    <blockquote class="rc-review">${c.review}</blockquote>
    <div class="rc-msg" data-msg>${c.draft}</div>
    <div class="rc-foot">
      <span class="rc-chan">↳ ${c.channel}</span>
      <div class="rc-actions">
        <button class="btn-mini btn-approve" data-act="approve">Approve &amp; post</button>
        <button class="btn-mini btn-edit" data-act="edit">Edit</button>
        <button class="btn-mini btn-skip" data-act="skip">Skip</button>
      </div>
    </div>
  </li>`;
}

function askCard(c, i) {
  return `
  <li class="react-card rep-card" data-i="${i}" data-type="ask">
    <div class="rc-top">
      <span class="rc-av">★</span>
      <div class="rc-id">
        <span class="rc-name">${c.label}</span>
        <span class="rc-meta">5★ visits · ${c.when}</span>
      </div>
      <span class="rep-kind">Review ask</span>
    </div>
    <div class="rc-msg" data-msg>${c.draft}</div>
    <div class="rc-foot">
      <span class="rc-chan">${/sms/i.test(c.channel) ? "📱 " : "✉️ "}${c.channel} · ${c.count} guests</span>
      <div class="rc-actions">
        <button class="btn-mini btn-approve" data-act="approve">Approve &amp; send</button>
        <button class="btn-mini btn-edit" data-act="edit">Edit</button>
        <button class="btn-mini btn-skip" data-act="skip">Skip</button>
      </div>
    </div>
  </li>`;
}

function render() {
  document.body.dataset.theme = R.theme;
  document.title = `${R.label} · Reputation`;
  $("#bizName").textContent = R.label;
  $("#bizSwitch").value = R._id;
  $("#repNote").textContent = R.note;
  $("#rsRating").textContent = R.rating;
  $("#repList").innerHTML = R._items.map((c, i) => c.type === "reply" ? replyCard(c, i) : askCard(c, i)).join("");
  updateSummary();
  if (R.moreCount) { $("#repMore").hidden = false; $("#repMore").textContent = `+ ${R.moreCount} more in the full queue`; }
  else $("#repMore").hidden = true;
}

function updateSummary() {
  $("#rsCount").textContent = $$(".rep-card:not(.done)").length;
}

function approveCard(el, silent) {
  if (el.classList.contains("done")) return;
  const c = R._items[+el.dataset.i];
  el.classList.add("done");
  if (c.type === "reply") {
    postedReplies += 1;
    el.querySelector(".rc-actions").innerHTML = `<span class="sent-tag">Posted ✓</span>`;
    if (!silent) toast("Posted to Google ✓");
  } else {
    sentAsks += c.count || 1;
    el.querySelector(".rc-actions").innerHTML = `<span class="sent-tag">Sent ✓</span>`;
    if (!silent) toast(`Ask sent to <b>${c.count}</b> guests ✓`);
  }
  updateSummary();
  $("#repDone").hidden = false;
  $("#repDone").innerHTML = `★ <b>${postedReplies}</b> replies posted · <b>${sentAsks}</b> review asks sent`;
}

function onListClick(e) {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const el = btn.closest(".rep-card");
  const act = btn.dataset.act;
  if (act === "approve") approveCard(el);
  else if (act === "skip") { el.classList.add("done"); el.style.opacity = "0.4"; el.querySelector(".rc-actions").innerHTML = `<span class="skip-tag">Skipped</span>`; updateSummary(); }
  else if (act === "edit") {
    const msg = el.querySelector("[data-msg]");
    if (msg.getAttribute("contenteditable") === "true") { msg.removeAttribute("contenteditable"); btn.textContent = "Edit"; toast("Edit saved"); }
    else { msg.setAttribute("contenteditable", "true"); msg.focus(); btn.textContent = "Done"; }
  }
}

function load(id) {
  R = structuredClone(REPUTATION[id]); R._id = id; R._items = buildItems(R);
  postedReplies = 0; sentAsks = 0; $("#repDone").hidden = true;
  render();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#repList").addEventListener("click", onListClick);
  $("#approveAll").addEventListener("click", () => {
    $$(".rep-card:not(.done)").forEach((el, k) => setTimeout(() => approveCard(el, true), k * 120));
    setTimeout(() => toast(`★ All handled — <b>${postedReplies}</b> replies + <b>${sentAsks}</b> asks`), 200);
  });
  $("#bizSwitch").addEventListener("change", e => {
    localStorage.setItem("demoBiz", e.target.value);
    const url = new URL(location); url.searchParams.set("biz", e.target.value);
    history.replaceState({}, "", url); load(e.target.value);
  });
  load(pickBiz());
});
