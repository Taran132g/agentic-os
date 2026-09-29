/* ============================================================
   Daily Brief — the Digest agent's morning output per business.
   Plain-English "here's yesterday + what to do today", the way an
   owner would actually read it on their phone. Tied to the agents.
   ============================================================ */

const DIGESTS = {
  /* Trappe Tavern — NO POS, so the read is estimated from bookings + traffic */
  "trappe-tavern": {
    label: "Trappe Tavern", sub: "Daily Brief", theme: "tavern",
    date: "Last night · Friday", estimated: true,
    headline: "Your strongest Friday in three weeks.",
    message: [
      "Friday came in hot 🍺 Estimated ~$4,820 across about 182 covers — up 18% on last Friday.",
      "The 8–11pm block did most of the damage; you were slammed from 9 on. Wings led the kitchen again, and the seasonal draft moved fast at the bar.",
      "One soft spot — 3–5pm was dead, like most weekdays.",
    ],
    tiles: [
      { num: "~$4,820", lbl: "est. revenue", sub: "+18% vs last Fri" },
      { num: "182", lbl: "covers", sub: "~$34 avg tab" },
      { num: "9–11pm", lbl: "peak", sub: "staff heavy" },
      { num: "Wings", lbl: "top seller", sub: "3 Fridays running" },
    ],
    todo: "Tonight's the same shape — but after 9 you run out of host coverage, and that's exactly when calls ring out. Worth a second host, or let the AI catch the overflow.",
    tie: "Missed-Call / AI Voice is covering the phone after 9pm.",
  },

  /* TruFit — gym read is attendance + retention, not daily sales */
  "trufit": {
    label: "TruFit", sub: "Daily Brief", theme: "fitness",
    date: "Yesterday · Thursday", estimated: false,
    headline: "Busy Thursday — evening classes packed out.",
    message: [
      "182 check-ins yesterday, up 6% on last Thursday 💪",
      "6am bootcamp and the 6pm HIIT both filled with waitlists. 3 free trials booked straight off the website.",
      "Watch list: 18 members haven't scanned in for 2+ weeks — the window right before they cancel.",
    ],
    tiles: [
      { num: "182", lbl: "check-ins", sub: "+6% vs last Thu" },
      { num: "6am · 6pm", lbl: "peak classes", sub: "both waitlisted" },
      { num: "3", lbl: "trials booked", sub: "from Google" },
      { num: "18", lbl: "at-risk", sub: "Reactivation on it" },
    ],
    todo: "Open a second 6pm slot on Thursdays — it waitlists every week. You're leaving spots, and money, on the table.",
    tie: "Reactivation is drafting win-backs for the 18 fading members.",
  },

  /* Advanced Dentistry — production + chair utilization + recall pipeline */
  "advanced-dentistry": {
    label: "Advanced Dentistry", sub: "Daily Brief", theme: "clinic",
    date: "Yesterday · Thursday", estimated: false,
    headline: "Full chairs — but two no-shows bit.",
    message: [
      "Thursday produced about $3,140 across 9 of 11 chairs — 81% utilization 🦷",
      "Two no-shows cost roughly $440 of empty chair time. 3 new patients came in from Google.",
      "The big one: 23 patients are overdue for their 6-month recall — that's about $4,600 sitting in the list.",
    ],
    tiles: [
      { num: "~$3,140", lbl: "production", sub: "Thursday" },
      { num: "81%", lbl: "chair use", sub: "9 of 11 chairs" },
      { num: "2", lbl: "no-shows", sub: "≈ $440 lost" },
      { num: "23", lbl: "overdue recalls", sub: "≈ $4,600" },
    ],
    todo: "Fill tomorrow's 2 open afternoon chairs straight from the recall list — Reminders is already confirming the booked ones so you don't eat more no-shows.",
    tie: "Reminders + Recall are working tomorrow's book.",
  },
};
