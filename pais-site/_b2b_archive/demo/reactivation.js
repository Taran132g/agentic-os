/* Reactivation / win-back queue — readable list of drafted messages. */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const money = (n) => "$" + Number(n).toLocaleString("en-US");

let R, recovered = 0, sentCount = 0;

function pickBiz() {
  const url = new URLSearchParams(location.search).get("biz");
  const stored = localStorage.getItem("demoBiz");
  const b = (url && REACTIVATION[url]) ? url : (stored && REACTIVATION[stored]) ? stored : "trappe-tavern";
  localStorage.setItem("demoBiz", b);
  return b;
}
function initials(name) {
  return name.replace(/^The\s+/, "").split(/\s|&/).filter(Boolean).slice(0, 2).map(w => w[0]).join("").toUpperCase();
}
function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.innerHTML = msg;
  $("#toaster").appendChild(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 360); }, 2600);
}

function cardHTML(c, i) {
  const sms = /sms/i.test(c.channel);
  return `
  <li class="react-card" data-i="${i}">
    <div class="rc-top">
      <span class="rc-av">${initials(c.name)}</span>
      <div class="rc-id">
        <span class="rc-name">${c.name}</span>
        <span class="rc-meta">${c.meta}</span>
      </div>
      <span class="rc-value">${c.value ? "≈ " + money(c.value) : "—"}</span>
    </div>
    <div class="rc-msg" data-msg>${c.message}</div>
    <div class="rc-foot">
      <span class="rc-chan ${sms ? "sms" : "email"}">${sms ? "📱 " : "✉️ "}${c.channel}</span>
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
  document.title = `${R.label} · Win-back queue`;
  $("#bizName").textContent = R.label;
  $("#bizSwitch").value = R._id;
  $("#reactKind").textContent = R.kind;
  $("#reactNote").textContent = R.note;
  const dl = $("#digestLink"); if (dl) dl.href = `digest.html?biz=${R._id}`;
  $("#reactList").innerHTML = R.customers.map(cardHTML).join("");
  updateSummary();
  if (R.moreCount) { $("#reactMore").hidden = false; $("#reactMore").textContent = `+ ${R.moreCount} more in the full queue`; }
  else $("#reactMore").hidden = true;
}

function updateSummary() {
  const remaining = $$(".react-card:not(.done)");
  const count = remaining.length;
  const val = remaining.reduce((a, el) => a + (R.customers[+el.dataset.i].value || 0), 0);
  $("#rsCount").textContent = count;
  $("#rsValue").textContent = money(val);
}

function sendCard(el, silent) {
  if (el.classList.contains("done")) return;
  const c = R.customers[+el.dataset.i];
  el.classList.add("done");
  el.querySelector(".rc-actions").innerHTML = `<span class="sent-tag">Sent ✓</span>`;
  recovered += c.value || 0; sentCount += 1;
  updateSummary();
  $("#reactRecovered").hidden = false;
  $("#reactRecovered").innerHTML = `🎉 <b>${sentCount}</b> win-backs sent · <b>${money(recovered)}</b> back in play`;
  if (!silent) toast(c.value ? `Sent ✓ <b>${money(c.value)}</b> in play` : "Sent ✓");
}

function onListClick(e) {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const el = btn.closest(".react-card");
  const act = btn.dataset.act;
  if (act === "approve") sendCard(el);
  else if (act === "skip") { el.classList.add("done"); el.style.opacity = "0.4"; el.querySelector(".rc-actions").innerHTML = `<span class="skip-tag">Skipped</span>`; updateSummary(); }
  else if (act === "edit") {
    const msg = el.querySelector("[data-msg]");
    if (msg.getAttribute("contenteditable") === "true") { msg.removeAttribute("contenteditable"); btn.textContent = "Edit"; toast("Edit saved"); }
    else { msg.setAttribute("contenteditable", "true"); msg.focus(); btn.textContent = "Done"; }
  }
}

function load(id) {
  R = structuredClone(REACTIVATION[id]); R._id = id;
  recovered = 0; sentCount = 0; $("#reactRecovered").hidden = true;
  render();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#reactList").addEventListener("click", onListClick);
  $("#approveAll").addEventListener("click", () => {
    $$(".react-card:not(.done)").forEach((el, k) => setTimeout(() => sendCard(el, true), k * 120));
    setTimeout(() => toast(`🎉 All sent — <b>${money(recovered)}</b> back in play`), 200);
  });
  $("#bizSwitch").addEventListener("change", e => {
    localStorage.setItem("demoBiz", e.target.value);
    const url = new URL(location); url.searchParams.set("biz", e.target.value);
    history.replaceState({}, "", url); load(e.target.value);
  });
  load(pickBiz());
});
