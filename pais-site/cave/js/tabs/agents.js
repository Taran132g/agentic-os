// 01 AGENTS — what runs on your behalf: schedule, how it works, current output, and mission progress.
// Read-only: agents run on their own schedules, never from the site.
import { h, card, light, when, until, ago, head, meter, num, errorCard } from "../ui.js";
import { renderMarkdown } from "../md.js";

const GOAL_LABEL = { trading: "TRADING", job: "JOB", gym: "GYM" };

function missionStrip(snap) {
  const r = snap.portfolio || snap.roi || {}, j = snap.job || {}, g = snap.gym || {};
  const jc = j.counts || {};
  const gw = g.this_week || {};
  return h("div", { class: "grid g-3" },
    card(snap.portfolio ? "Mission · Trading (whole portfolio)" : "Mission · Trading", meter("MTD return", r.mtd_pct == null ? "—" : `${r.mtd_pct.toFixed(1)}%`, `${r.target_pct ?? 20}%`, r.progress,
      { sub: r.hit ? "Target cleared this month." : r.target_balance ? `Target ${Math.round(r.target_balance).toLocaleString()} · ${snap.roi?.days_left ?? "—"} days left` : "" }), { cls: "hud" }),
    card("Mission · Internship", j.status === "ok"
      ? meter("Applications", num(jc.applications), num(j.goals?.applications_goal), jc.applications / (j.goals?.applications_goal || 1),
        { sub: `${num(jc.interviews)} interviews · ${num(jc.offers)} offers${j.days_to_deadline != null ? ` · ${j.days_to_deadline} days to deadline` : ""}` })
      : h("div", {}, h("div", { class: "kpi-value" }, "Gmail offline"), h("p", { class: "kpi-sub" }, "Reconnect Gmail to track applications.")),
      { cls: "hud" }),
    card("Mission · Gym", g.status === "ok"
      ? meter("Workouts this week", num(gw.workouts), num(g.goals?.workouts_per_week), g.progress?.workouts, { sub: `${num(gw.azm)} active zone min this week` })
      : h("div", {}, h("div", { class: "kpi-value" }, "Awaiting data"), h("p", { class: "kpi-sub" }, "First Fitbit export lands after Friday's audit.")),
    { cls: "hud" }));
}

function agentCard(a) {
  const dots = (a.history || []).slice().reverse().map(r =>
    h("i", { class: r.status === "done" ? "ok" : r.status === "failed" ? "bad" : "none", title: `${when(r.started)} · ${r.status}` }));
  const output = h("div", { class: "output", hidden: true });
  if (a.report) {
    const box = h("div", { class: "report clamp" });
    box.innerHTML = renderMarkdown(a.report);  // renderMarkdown escapes all input first
    output.append(box);
  } else if (a.log?.length) {
    output.append(h("pre", { class: "logbox" }, a.log.join("\n")));
  } else {
    output.append(h("p", { class: "kpi-sub" }, "Runs in the cloud; its results show up in the Job tab from your Gmail."));
  }
  const toggle = h("button", { class: "link-btn", type: "button", "aria-expanded": "false", onclick: e => {
    output.hidden = !output.hidden;
    e.currentTarget.setAttribute("aria-expanded", String(!output.hidden));
    e.currentTarget.textContent = output.hidden ? "CURRENT OUTPUT ▸" : "HIDE OUTPUT ▾";
  } }, "CURRENT OUTPUT ▸");
  const trouble = ["failed", "fault", "offline"].includes(a.status);
  return card(a.name, h("div", { class: "agent" },
    h("div", { class: "agent-top" },
      h("span", { class: "codename" }, a.codename),
      h("span", { class: "goal-tag" }, GOAL_LABEL[a.goal] || ""),
      h("span", { class: "spacer" }),
      light(a.status)),
    h("p", { class: "lede", style: "margin:0" }, a.what),
    h("ol", { class: "how" }, (a.how || []).map(step => h("li", {}, step))),
    h("dl", { class: "meta" },
      h("dt", {}, "Schedule"), h("dd", {}, a.schedule || "—"),
      a.last_run ? [h("dt", {}, "Last run"), h("dd", {}, `${when(a.last_run)} (${ago(a.last_run)})`)] : null,
      a.next_run ? [h("dt", {}, "Next run"), h("dd", {}, `${when(a.next_run)} (${until(a.next_run)})`)] : null,
      a.importer ? [h("dt", {}, "Mac import"), h("dd", {}, `${a.importer} · ${a.days ?? 0} days imported`)] : null,
      [h("dt", {}, "Site"), h("dd", {}, "refreshes when the run's output lands")]),
    dots.length ? h("div", { class: "dots", "aria-label": "Recent runs, oldest to newest" }, dots) : null,
    toggle, output), { cls: trouble ? "alert" : "" });
}

export default {
  id: "agents", label: "AGENTS",
  render(view, snap) {
    const roster = snap.agents;
    view.append(head("Operations", "Read-only", "Agents & missions",
      "Every agent runs on its own schedule. This is where you watch them work and see how close each mission is."));
    view.append(missionStrip(snap));
    if (!Array.isArray(roster)) {
      view.append(errorCard("Agents", roster?.error || "no data"));
      return "The agent roster isn't reporting, sir.";
    }
    view.append(h("div", { class: "grid g-3", style: "margin-top:14px" }, roster.map(agentCard)));
    const down = roster.filter(a => ["failed", "fault", "offline"].includes(a.status));
    return down.length
      ? `${down.map(a => a.codename).join(" and ")} reported trouble on the last run, sir. Everything else is on schedule.`
      : "All agents reporting for duty, sir.";
  },
};
