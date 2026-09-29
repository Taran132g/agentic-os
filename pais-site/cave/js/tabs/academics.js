// 05 ACADEMICS — Canvas homework to-do, test calendar, grades and the weekly class schedule.
// Data: Oracle pulls Canvas every 30 min (snapshot.academics); class times come from classes.json.
import { h, card, head, num, until } from "../ui.js";

const DAYS = [["M", "Mon"], ["T", "Tue"], ["W", "Wed"], ["R", "Thu"], ["F", "Fri"]];
const KIND_CLASS = { exam: "short", quiz: "flag", due: "", done: "long" };

const fmtDue = iso => new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const fmtClock = hhmm => { const [H, M] = hhmm.split(":").map(Number); return `${H % 12 || 12}:${String(M).padStart(2, "0")}${H < 12 ? "a" : "p"}`; };
const daysUntil = iso => Math.round((new Date(`${iso.slice(0, 10)}T12:00:00`) - new Date(new Date().toDateString() + " 12:00")) / 86400000);
const safeLink = (url, text) => /^https:\/\//.test(url || "") ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text) : text;

function setup(ac) {
  return card("Connect Canvas", h("ol", { class: "how" },
    h("li", {}, "canvas.psu.edu → Account → Settings → Approved Integrations → + New Access Token (name it PAIS)."),
    h("li", {}, "Give the token to Claude; it goes into ~/pais-cave/.env on the server as CANVAS_TOKEN, never into the site."),
    h("li", {}, "This tab fills in within 30 minutes, and the 9 am text starts listing homework and tests.")),
  { cls: ac.status === "error" ? "alert" : "" });
}

function nextTestCard(tests) {
  const t = tests.find(x => x.kind === "exam") || tests[0];
  if (!t) return card("Next test", h("p", { class: "empty" }, "No tests posted on Canvas."), { cls: "hud" });
  const n = daysUntil(t.due);
  return card("Next test", h("div", {},
    h("div", { class: "kpi-label" }, t.course),
    h("div", { class: `kpi-value ${n <= 3 ? "alarm" : ""}` }, n === 0 ? "TODAY" : `${n} day${n === 1 ? "" : "s"}`),
    h("div", { class: "kpi-sub" }, safeLink(t.url, t.title), ` · ${fmtDue(t.due)}`)), { cls: "hud" });
}

function workloadCard(ac) {
  const week = ac.todo.filter(i => !i.missing && daysUntil(i.due) <= 7).length;
  return card("Homework", h("div", {},
    h("div", { class: "kpi-label" }, "Due in the next 7 days"),
    h("div", { class: "kpi-value" }, num(week)),
    h("div", { class: `kpi-sub ${ac.missing ? "err" : ""}` }, ac.missing ? `${ac.missing} missing — clear these first` : "Nothing missing.")), { cls: "hud" });
}

function todayCard(ac) {
  const list = ac.classes_today || [];
  return card("Today's classes", list.length
    ? h("ul", { class: "class-list" }, list.map(c => h("li", {},
      h("span", { class: "mono" }, `${fmtClock(c.start)}–${fmtClock(c.end || c.start)}`), h("strong", {}, c.course), c.room ? h("span", { class: "muted" }, c.room) : null)))
    : h("p", { class: "empty" }, (ac.timetable || []).length ? "No classes today." : "Class times not set yet."), { cls: "hud" });
}

function todoCard(ac) {
  return card(`To-do · ${ac.todo.length}`, ac.todo.length
    ? h("ul", { class: "todo" }, ac.todo.map(i => h("li", { class: i.missing ? "missing" : daysUntil(i.due) <= 1 ? "soon" : "" },
      h("span", { class: "todo-course mono" }, i.course),
      h("span", { class: "todo-title" }, safeLink(i.url, i.title)),
      h("span", { class: "todo-due" }, i.missing ? h("span", { class: "tag short" }, "Missing") : `${fmtDue(i.due)} · ${until(i.due)}`))))
    : h("p", { class: "empty" }, "All clear. Nothing due."));
}

function gradesCard(ac) {
  return card("Grades", h("div", { class: "grades" }, ac.grades.map(g => {
    const s = g.score;
    const state = s == null ? "far" : s >= 90 ? "done" : s >= 80 ? "near" : "far";
    return h("div", { class: `grade ${state}` },
      h("div", { class: "grade-top" }, h("strong", {}, g.course), h("span", { class: "mono" }, s == null ? "no grades yet" : `${num(s, 1)}%${g.grade ? ` · ${g.grade}` : ""}`)),
      h("div", { class: "meter-track" }, h("div", { class: "meter-fill", style: `transform:scaleX(${Math.min((s || 0) / 100, 1)})` })));
  })));
}

function testsCard(tests) {
  return card(`Test calendar · ${tests.length}`, tests.length
    ? h("ol", { class: "tests" }, tests.map(t => h("li", {},
      h("span", { class: `tag ${KIND_CLASS[t.kind]}` }, t.kind === "exam" ? "Exam" : "Quiz"),
      h("span", { class: "mono" }, new Date(t.due).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })),
      h("strong", {}, t.course), safeLink(t.url, t.title), h("span", { class: "muted" }, until(t.due)))))
    : h("p", { class: "empty" }, "No tests or quizzes on Canvas yet."));
}

function monthCard(ac) {
  const todayIso = ac.today;
  const cells = (ac.calendar || []).map(d => h("div", { class: `cal-day ${d.date === todayIso ? "today" : ""} ${d.date < todayIso ? "past" : ""}` },
    h("div", { class: "cal-num" }, Number(d.date.slice(8))),
    d.items.slice(0, 3).map(i => h("div", { class: `cal-item ${i.kind}`, title: `${i.course} · ${i.title}` }, `${i.course.split(" ").pop()} ${i.title}`)),
    d.items.length > 3 ? h("div", { class: "cal-more" }, `+${d.items.length - 3}`) : null));
  return card("Due dates & tests · 6 weeks", h("div", {},
    h("div", { class: "cal-grid cal-head" }, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(d => h("div", {}, d))),
    h("div", { class: "cal-grid" }, cells),
    h("div", { class: "cal-legend" }, h("span", { class: "cal-item exam" }, "Exam"), h("span", { class: "cal-item quiz" }, "Quiz"),
      h("span", { class: "cal-item due" }, "Due"), h("span", { class: "cal-item done" }, "Submitted"))));
}

function weekCard(ac) {
  const tt = ac.timetable || [];
  if (!tt.length) return card("Weekly schedule", h("p", { class: "empty" }, "Send Claude your class times (or a LionPATH screenshot) to fill this in."));
  const todayLetter = "UMTWRFS"[new Date().getDay()];
  return card("Weekly schedule", h("div", { class: "week" }, DAYS.map(([letter, name]) => h("div", { class: `week-col ${letter === todayLetter ? "today" : ""}` },
    h("div", { class: "week-name" }, name),
    tt.filter(c => (c.days || "").toUpperCase().includes(letter)).sort((a, b) => a.start.localeCompare(b.start))
      .map(c => h("div", { class: "week-block" }, h("strong", {}, c.course), h("span", { class: "mono" }, `${fmtClock(c.start)}–${fmtClock(c.end || c.start)}`),
        c.room ? h("span", { class: "muted" }, c.room) : null))))));
}

export default {
  id: "academics", label: "ACADEMICS",
  render(view, snap) {
    const ac = snap.academics || { status: "no_token" };
    const ok = ac.status === "ok";
    const next = ok ? (ac.tests.find(t => t.kind === "exam") || ac.tests[0]) : null;
    view.append(head("Fall 2026", next ? `${next.course} in ${daysUntil(next.due)}d` : "Penn State", "Academics",
      ok ? `From Canvas · synced ${new Date(ac.fetched_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}${ac.warning ? ` · ${ac.warning}` : ""}`
        : "Homework, tests, grades and your class schedule, straight from Canvas."));
    if (!ok) {
      view.append(setup(ac), h("div", { style: "margin-top:14px" }, weekCard(ac)));
      return ac.status === "error" ? `Canvas is refusing me, sir. ${ac.error}` : "Canvas isn't connected yet, sir. I need an access token.";
    }
    view.append(
      h("div", { class: "grid g-3" }, nextTestCard(ac.tests), workloadCard(ac), todayCard(ac)),
      h("div", { class: "grid g-hero", style: "margin-top:14px" }, todoCard(ac), gradesCard(ac)),
      h("div", { class: "grid g-hero", style: "margin-top:14px" }, monthCard(ac), testsCard(ac.tests)),
      h("div", { style: "margin-top:14px" }, weekCard(ac)));
    if (ac.missing) return `${ac.missing} assignment${ac.missing === 1 ? " is" : "s are"} missing, sir. I'd start there.`;
    return next ? `${next.course} ${next.title} is in ${daysUntil(next.due)} days, sir.` : "Nothing urgent on the academic front, sir.";
  },
};
