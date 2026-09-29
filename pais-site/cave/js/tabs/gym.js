// 04 GYM — training and recovery goals from the Fitbit Air (via the weekly Takeout import).
import { h, card, head, num, day, meter, barChart, table, errorCard } from "../ui.js";

function awaiting(g) {
  const goals = g.goals || {};
  return [
    card("Weekly targets", h("div", { class: "grid g-4" },
      [["Workouts", `${goals.workouts_per_week}/week`], ["Active zone min", `${goals.active_zone_min_per_week}/week`],
        ["Steps", `${num(goals.steps_per_day)}/day`], ["Sleep", `${goals.sleep_hours} h/night`]].map(([l, v]) =>
        h("div", {}, h("div", { class: "kpi-label" }, l), h("div", { class: "kpi-value" }, v))))),
    h("div", { style: "margin-top:14px" }, card("How your data gets here", h("ol", { class: "how" },
      h("li", {}, "Fridays after the Yubit audit, Claude downloads last week's Fitbit export from Google Takeout and requests the next one."),
      h("li", {}, "Your Mac imports the zip: daily metrics plus every logged workout."),
      h("li", {}, "This tab fills in within 15 minutes of the import.")))),
  ];
}

export default {
  id: "gym", label: "GYM",
  render(view, snap) {
    const g = snap.gym || {};
    view.append(head("Training", "Fitbit Air", "Gym tracker & goals",
      g.status === "ok" ? `${g.days_logged} days logged · latest ${day(g.latest?.date)}` : "Waiting on the first Fitbit export."));
    if (g.status === "error") { view.append(errorCard("Gym", g.error)); return "The Fitbit feed is down, sir."; }
    if (g.status !== "ok") {
      view.append(...awaiting(g));
      return "No readings yet, sir. Your first Fitbit export arrives after Friday's audit.";
    }
    const w = g.this_week, goals = g.goals, p = g.progress;
    view.append(h("div", { class: "grid g-4" },
      card("Workouts", meter("This week", num(w.workouts), num(goals.workouts_per_week), p.workouts, { sub: `${num(w.workout_minutes)} min trained` }), { cls: "hud" }),
      card("Active zone minutes", meter("This week", num(w.azm), num(goals.active_zone_min_per_week), p.azm), { cls: "hud" }),
      card("Steps", meter("Daily average", num(w.steps_avg), num(goals.steps_per_day), p.steps, { sub: `${g.steps_streak}-day streak at goal` }), { cls: "hud" }),
      card("Sleep", meter("Nightly average", w.sleep_avg == null ? "—" : `${num(w.sleep_avg, 1)} h`, `${goals.sleep_hours} h`, p.sleep), { cls: "hud" })));
    const weeks = g.weeks.map((wk, i, arr) => ({ label: `Week of ${day(wk.week)}`, value: wk.workouts, current: i === arr.length - 1 }));
    const azm = g.weeks.map((wk, i, arr) => ({ label: `Week of ${day(wk.week)}`, value: wk.azm, current: i === arr.length - 1 }));
    view.append(h("div", { class: "grid g-hero", style: "margin-top:14px" },
      card("Workouts per week · gold = goal met", barChart(weeks, { goal: goals.workouts_per_week, label: "Workouts per week", format: v => `${v} workouts` })),
      card("Active zone minutes per week", barChart(azm, { goal: goals.active_zone_min_per_week, label: "Active zone minutes per week", format: v => `${v} min` }))));
    view.append(h("div", { style: "margin-top:14px" }, card("Recent workouts", (g.workouts || []).length
      ? table(["Date", "Workout", "Minutes", "Avg HR", "Calories", "Zone min"],
        g.workouts.map(x => [day(x.date), h("strong", {}, x.activity), num(x.minutes), x.avg_hr ? `${num(x.avg_hr)} bpm` : "—", num(x.calories), num(x.azm)]),
        { numeric: [2, 3, 4, 5] })
      : h("p", { class: "empty" }, "No workouts logged in Fitbit yet. Start one from the app so it shows up here."))));
    const left = Math.max(0, goals.workouts_per_week - w.workouts);
    return left ? `${w.workouts} workouts this week, sir. ${left} more to hit your target.` : "Weekly training target met, sir. Well done.";
  },
};
