// 03 JOB — end goal: a Summer 2027 internship offer, tracked straight from Gmail
// (application confirmations, assessments, interviews, rejections, offers).
import { api, h, card, head, num, day, when, ago, meter, barChart, table } from "../ui.js";

const STAGE_LABEL = { submitted: "Applied", assessment: "Assessment", interview: "Interview", rejected: "Rejected", offer: "Offer" };
const STAGE_CLASS = { submitted: "", assessment: "flag", interview: "long", rejected: "short", offer: "long" };

const stageTag = stage => h("span", { class: `tag ${STAGE_CLASS[stage] || ""}` }, STAGE_LABEL[stage] || stage);

function notConnected(j) {
  return card("Gmail not connected", h("div", {},
    h("p", { class: "err" }, j.error),
    h("ol", { class: "how" },
      h("li", {}, "Go to myaccount.google.com/apppasswords and create an app password named PAIS."),
      h("li", {}, "Put it in ~/agentic_os/.env as GMAIL_APP_PASSWORD (replace the old one)."),
      h("li", {}, "This tab fills in within 30 minutes."))), { cls: "alert" });
}

const CATS = [["finance", "Finance"], ["software", "Software"]];

// Today's 6 applications (scouted on the Mac at 7am). Gmail confirmations tick them off; the
// checkbox is the manual override. 8pm text fires if any are still open.
function missionCard(snap) {
  const d = snap.daily || {};
  if (!d.total) {
    return card("Today's mission", h("p", { class: "empty" },
      d.missing_list ? "Today's six jobs haven't been scouted yet. The Mac runs the scout at 7am (or when it wakes)." : "No list today."));
  }
  const copy = h("button", { class: "btn", type: "button", onclick: async () => {
    try { await navigator.clipboard.writeText(d.claude_prompt || ""); copy.textContent = "Copied. Paste into Claude in Chrome"; }
    catch { copy.textContent = "Copy failed (clipboard blocked)"; }
  } }, "Copy Claude-in-Chrome prompt");
  const toggle = async (job, box) => {
    box.disabled = true;
    try {
      snap.daily = await api("/daily/mark", { method: "POST", body: JSON.stringify({ date: d.date, url: job.url, done: !job.done }) });
      node.replaceWith(missionCard(snap));
    } catch (err) { box.disabled = false; box.title = err.message; }
  };
  const row = job => {
    const box = h("button", { class: `check ${job.done ? "on" : ""}`, type: "button", "aria-pressed": String(job.done),
      "aria-label": `${job.done ? "Unmark" : "Mark"} ${job.company} as applied`, disabled: job.via === "email" ? true : null,
      title: job.via === "email" ? "Confirmed by a Gmail confirmation" : "Mark applied" });
    box.addEventListener("click", () => toggle(job, box));
    return h("li", { class: job.done ? "done" : "" }, box,
      h("div", { class: "mission-text" },
        h("strong", {}, job.company), h("span", {}, job.role),
        h("span", { class: "muted" }, [job.location, job.why].filter(Boolean).join(" · "))),
      job.via === "email" ? h("span", { class: "tag long" }, "Gmail ✓") : null,
      /^https:\/\//.test(job.url) ? h("a", { class: "btn ghost", href: job.url, target: "_blank", rel: "noopener noreferrer" }, "Open ↗") : null);
  };
  const node = card(`Today's mission · ${d.done}/${d.total} applied`, h("div", {},
    meter("Applications today", num(d.done), num(d.total), d.done / d.total,
      { sub: d.complete ? "Done for the day." : "If these aren't all done by 8pm you'll get a text." }),
    h("div", { class: "grid mission" }, CATS.map(([cat, label]) => h("div", {},
      h("div", { class: "kpi-label" }, label),
      h("ul", { class: "mission-list" }, d.jobs.filter(j => j.category === cat).map(row)))))),
  { cls: d.complete ? "hud" : "hud alert-soft", action: copy });
  return node;
}

export default {
  id: "job", label: "JOB",
  render(view, snap) {
    const j = snap.job || {};
    const g = j.goals || {};
    view.append(head("End goal", j.days_to_deadline != null ? `${j.days_to_deadline} days left` : "Summer 2027", j.target || "Land the internship",
      j.status === "ok" ? `Tracked from Gmail · last scan ${ago(j.scanned_at)} · deadline ${day(j.deadline)}` : `Deadline ${day(j.deadline)}`));
    view.append(h("div", { style: "margin-bottom:14px" }, missionCard(snap)));
    if (j.status === "gmail_error") {
      view.append(notConnected(j));
      return "I can't reach your Gmail, sir. The app password needs replacing.";
    }
    if (j.status !== "ok") {
      view.append(card("Job", h("p", { class: "err" }, j.error || "No data yet.")));
      return "The job tracker isn't reporting, sir.";
    }
    const c = j.counts;
    view.append(h("div", { class: "grid g-3" },
      card("Applications", meter("Confirmed by email", num(c.applications), num(g.applications_goal), c.applications / (g.applications_goal || 1),
        { sub: `${num(j.applied_this_week)} this week · ${num(c.active)} still open` }), { cls: "hud" }),
      card("Interviews", meter("Reached", num(c.interviews), num(g.interviews_goal), c.interviews / (g.interviews_goal || 1),
        { sub: `${num(c.assessments)} assessments · ${num(c.rejections)} rejections` }), { cls: "hud" }),
      card("Offers", meter("Received", num(c.offers), num(g.offers_goal), c.offers / (g.offers_goal || 1),
        { sub: c.offers >= (g.offers_goal || 1) ? "Mission accomplished." : "The one that counts." }), { cls: "hud" })));
    const weekly = (j.weekly || []).map((w, i, arr) => ({ label: `Week of ${day(w.week)}`, value: w.applied, current: i === arr.length - 1 }));
    view.append(h("div", { class: "grid g-hero", style: "margin-top:14px" },
      card("Latest from your inbox", (j.recent || []).length
        ? table(["When", "Company", "Update", "Subject"], j.recent.map(e => [day(e.date), h("strong", {}, e.company), stageTag(e.stage), e.subject]))
        : h("p", { class: "empty" }, "No application emails in the last 6 months.")),
      card("New applications per week", h("div", {}, barChart(weekly, { label: "Applications per week", format: v => `${v} applications` }),
        h("div", { class: "axis-note" }, h("span", {}, day(j.weekly?.[0]?.week)), h("span", {}, "this week"))))));
    view.append(h("div", { style: "margin-top:14px" }, card(`Every company · ${(j.companies || []).length}`, (j.companies || []).length
      ? table(["Company", "Status", "Furthest stage", "Last email", "Latest subject"],
        j.companies.map(r => [h("strong", {}, r.company), stageTag(r.stage), STAGE_LABEL[r.reached], when(r.last), r.subject]))
      : h("p", { class: "empty" }, "Nothing yet."))));
    return c.interviews
      ? `${c.interviews} interviews reached, sir, and ${c.active} applications still open.`
      : `${c.applications} applications confirmed and ${c.active} still open, sir. The first interview is the one to chase.`;
  },
};
