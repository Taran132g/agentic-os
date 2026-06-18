/* ============================================================
   Per-business Google presence analytics (Google Business Profile
   insights — searches, views, calls, directions, reviews). This is
   what actually matters for a local business and ties straight to
   the agents. Numbers are illustrative; in production they come from
   the Google Business Profile Performance API.
   ============================================================ */

const ANALYTICS = {
  /* ---------------- Trappe Tavern ---------------- */
  "trappe-tavern": {
    label: "Trappe Tavern", sub: "Google presence · last 30 days", theme: "tavern",
    kpis: {
      searches: { value: 4210, delta: 12 },
      views:    { value: 6540, delta: 8 },
      actions:  { value: 2180, delta: 15 },
    },
    trend: [180,165,172,190,240,310,260,175,168,180,195,250,330,275,182,170,178,200,255,340,280,185,175,185,205,260,350,290,188,178],
    searchTypes: [
      { label: "Discovery", value: 2210, color: "var(--brass)" },
      { label: "Direct", value: 1750, color: "var(--blue)" },
      { label: "Branded", value: 250, color: "var(--green)" },
    ],
    platform: { search: 2460, maps: 4080 },
    actions: [
      { label: "Calls", value: 540, color: "var(--green)", tie: "missed-call" },
      { label: "Directions", value: 1180, color: "var(--brass)" },
      { label: "Website clicks", value: 410, color: "var(--blue)" },
      { label: "Messages", value: 50, color: "var(--text-dim)" },
    ],
    topQueries: [
      { q: "trappe tavern", count: 1420 },
      { q: "bars near me", count: 680 },
      { q: "restaurants trappe pa", count: 520 },
      { q: "happy hour near me", count: 410 },
      { q: "wings near me", count: 300 },
    ],
    reviews: { rating: 4.3, count: 612, last30: 9, replied: 41 },
    insights: [
      "<b>540 people called you straight from Google</b> this month. On a packed Friday some of those ring out — that's exactly what the <b>Missed-Call</b> agent (or the AI receptionist) catches.",
      "<b>2,210 discovery searches</b> were people who didn't know your name and found you anyway. More 5★ reviews push you higher there — that's the <b>Reputation</b> agent's job.",
    ],
  },

  /* ---------------- TruFit ---------------- */
  "trufit": {
    label: "TruFit", sub: "Google presence · last 30 days", theme: "fitness",
    kpis: {
      searches: { value: 3120, delta: 18 },
      views:    { value: 4360, delta: 11 },
      actions:  { value: 1540, delta: 9 },
    },
    trend: [210,205,198,190,175,150,120,215,208,200,192,178,152,118,220,210,202,195,180,155,122,225,214,205,198,182,158,125,228,216],
    searchTypes: [
      { label: "Discovery", value: 1940, color: "var(--brass)" },
      { label: "Direct", value: 980, color: "var(--blue)" },
      { label: "Branded", value: 200, color: "var(--green)" },
    ],
    platform: { search: 1820, maps: 2540 },
    actions: [
      { label: "Website clicks", value: 690, color: "var(--blue)" },
      { label: "Directions", value: 520, color: "var(--brass)" },
      { label: "Calls", value: 280, color: "var(--green)", tie: "missed-call" },
      { label: "Messages", value: 50, color: "var(--text-dim)" },
    ],
    topQueries: [
      { q: "gym near me", count: 760 },
      { q: "trufit", count: 540 },
      { q: "gyms phoenixville", count: 430 },
      { q: "personal trainer near me", count: 360 },
      { q: "24 hour gym", count: 290 },
    ],
    reviews: { rating: 4.6, count: 318, last30: 14, replied: 22 },
    insights: [
      "<b>690 people clicked through to your site</b> — mostly checking membership. The <b>Reactivation</b> agent makes sure the ones who sign up and fade don't slip away.",
      "<b>280 calls</b> came from Google, many after the desk closed. <b>Missed-Call / AI Voice</b> turns those into booked tours instead of voicemails.",
    ],
  },

  /* ---------------- Advanced Dentistry ---------------- */
  "advanced-dentistry": {
    label: "Advanced Dentistry", sub: "Google presence · last 30 days", theme: "clinic",
    kpis: {
      searches: { value: 2740, delta: 9 },
      views:    { value: 3980, delta: 7 },
      actions:  { value: 1210, delta: 13 },
    },
    trend: [150,165,170,160,155,60,40,158,170,175,165,160,62,42,162,172,178,168,162,64,44,165,175,180,170,165,66,46,168,176],
    searchTypes: [
      { label: "Discovery", value: 1810, color: "var(--brass)" },
      { label: "Direct", value: 760, color: "var(--blue)" },
      { label: "Branded", value: 170, color: "var(--green)" },
    ],
    platform: { search: 2180, maps: 1800 },
    actions: [
      { label: "Calls", value: 460, color: "var(--green)", tie: "missed-call" },
      { label: "Directions", value: 380, color: "var(--brass)" },
      { label: "Website clicks", value: 320, color: "var(--blue)" },
      { label: "Messages", value: 50, color: "var(--text-dim)" },
    ],
    topQueries: [
      { q: "dentist near me", count: 690 },
      { q: "advanced dentistry phoenixville", count: 410 },
      { q: "emergency dentist", count: 320 },
      { q: "teeth cleaning near me", count: 280 },
      { q: "new patient dentist", count: 240 },
    ],
    reviews: { rating: 4.8, count: 204, last30: 11, replied: 38 },
    insights: [
      "<b>460 calls from Google</b> — a missed lunchtime call is a lost new patient worth thousands. <b>Missed-Call / AI Voice</b> answers every one.",
      "<b>1,810 discovery searches</b> for 'dentist near me' and similar. Your 4.8★ is your edge — the <b>Reputation</b> agent keeps it climbing.",
    ],
  },
};
