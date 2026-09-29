/* Daily Brief — render the Digest agent's morning output per business. */

const $ = (s) => document.querySelector(s);

function pickBiz() {
  const url = new URLSearchParams(location.search).get("biz");
  const stored = localStorage.getItem("demoBiz");
  const b = (url && DIGESTS[url]) ? url : (stored && DIGESTS[stored]) ? stored : "trappe-tavern";
  localStorage.setItem("demoBiz", b);
  return b;
}

function load(id) {
  const d = DIGESTS[id];
  document.body.dataset.theme = d.theme;
  document.title = `${d.label} · Daily Brief`;
  $("#bizName").textContent = d.label;
  $("#bizSwitch").value = id;
  $("#briefDate").textContent = d.date;
  $("#estBadge").hidden = !d.estimated;
  $("#briefHeadline").textContent = d.headline;
  $("#msgBody").innerHTML = d.message.map(p => `<p>${p}</p>`).join("");
  $("#briefTiles").innerHTML = d.tiles.map(t => `
    <div class="brief-tile">
      <span class="bt-num">${t.num}</span>
      <span class="bt-lbl">${t.lbl}</span>
      <span class="bt-sub">${t.sub}</span>
    </div>`).join("");
  $("#briefTodo").textContent = d.todo;
  $("#briefTie").innerHTML = `<b>On it:</b> ${d.tie}`;
  // keep the business selection when jumping to analytics
  const q = `?biz=${id}`;
  const af = $("#anaFoot"); if (af) af.href = "analytics.html" + q;
}

document.addEventListener("DOMContentLoaded", () => {
  $("#bizSwitch").addEventListener("change", e => {
    localStorage.setItem("demoBiz", e.target.value);
    const url = new URL(location); url.searchParams.set("biz", e.target.value);
    history.replaceState({}, "", url);
    load(e.target.value);
  });
  load(pickBiz());
});
