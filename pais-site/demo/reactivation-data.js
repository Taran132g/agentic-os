/* ============================================================
   Reactivation queue per business — the win-back messages the
   agent drafted for customers who've drifted away. A clean, readable
   list so an owner (and you, mid-demo) can see exactly what would go out.
   ============================================================ */

const REACTIVATION = {
  /* Trappe Tavern — regulars from the reservation log, quiet 40+ days */
  "trappe-tavern": {
    label: "Trappe Tavern", theme: "tavern",
    kind: "Regulars who've drifted away",
    note: "Pulled from your reservation log — haven't been in for 40+ days.",
    moreCount: 0,
    customers: [
      { name: "Dave Mercer", meta: "Friday regular · last in 6 weeks ago · ~$52 a visit", channel: "Email", value: 80,
        message: "Dave — nobody's been holding down your Friday stool and it's not the same without you. Swing in this week and your first round's on me. — Tom" },
      { name: "The Hendersons", meta: "Sunday dinner regulars · gone 7 weeks", channel: "Email", value: 95,
        message: "Hey folks — the Sunday table's missed you two. Come by this weekend and dessert's on the house. — Trappe Tavern" },
      { name: "Mike Russo", meta: "Bar regular · 8 weeks · ~$38 a visit", channel: "Owner-send SMS", value: 60,
        message: "Mike! Bar's been way too quiet without you. Next wing basket's on us if you stop in this week. — Tom" },
      { name: "Sarah & Jen", meta: "Thursday trivia team · 6 weeks", channel: "Email", value: 70,
        message: "Trivia hasn't been the same without your team 🍺 Come defend your title Thursday — first pitcher's on us." },
      { name: "Tom Becker", meta: "Lunch regular · 9 weeks", channel: "Email", value: 55,
        message: "Haven't seen you at lunch in a bit! Your usual's waiting — pop in this week and grab a free app. — Trappe Tavern" },
      { name: "The Walsh Family", meta: "Weekend dinner · 7 weeks", channel: "Email", value: 110,
        message: "The back booth's been missing the Walshes. Come in this weekend and the kids' meals are on us. — Tom" },
      { name: "Carlos Mendez", meta: "Happy hour regular · 6 weeks", channel: "Owner-send SMS", value: 68,
        message: "Carlos — happy hour's not the same without you at the rail. Stop by this week, first one's on me. — Tom" },
    ],
  },

  /* TruFit — members with no check-in in 14+ days (churn window) */
  "trufit": {
    label: "TruFit", theme: "fitness",
    kind: "Members who've gone quiet",
    note: "No check-in in 14+ days — the window right before they cancel. From Mindbody.",
    moreCount: 11,
    customers: [
      { name: "Jordan Kim", meta: "Last check-in 18 days ago · was 5×/week", channel: "Email", value: 220,
        message: "We've missed you at TruFit, Jordan 💪 Whatever knocked you off track, getting back is easier than you think. Come in this week and your next small-group session is on us. — Mara" },
      { name: "Aisha Patel", meta: "16 days · evening HIIT regular", channel: "Owner-send SMS", value: 220,
        message: "Hey Aisha! The 6pm crew's been asking about you. Your spot's still here — come back this week and I'll throw in a free PT session. — Mara" },
      { name: "Marcus Lee", meta: "21 days · was on a 40-day streak", channel: "Email", value: 220,
        message: "Marcus — that 40-day streak was no joke and we'd hate to see it end here. Back in this week = next month's classes half off. Let's go. — Mara" },
      { name: "Bella Soto", meta: "15 days · new member, 2 months in", channel: "Email", value: 180,
        message: "Bella! The first couple months are the hardest — don't quit now. Come to Saturday's intro class (on us) and we'll rebuild the habit together. — Mara" },
      { name: "Chris Day", meta: "19 days · 6am bootcamp", channel: "Owner-send SMS", value: 220,
        message: "Chris — 6am bootcamp misses you (and honestly so does the leaderboard). Back this week and your next month's free. — Mara" },
      { name: "Nina Brooks", meta: "14 days · just starting to fade", channel: "Email", value: 200,
        message: "Hey Nina — noticed it's been a couple weeks. No guilt trips, just a reminder your spot's here whenever you're ready. Free week to ease back in? — Mara" },
      { name: "Erik Tran", meta: "Free trial · claimed it, never came in", channel: "Email", value: 120,
        message: "Erik — your free trial at TruFit is still open! No pressure, even a tour counts. Want me to hold a spot in Saturday's intro class? — Mara" },
    ],
  },

  /* Advanced Dentistry — patients overdue for 6-month recall (HIPAA-safe: logistics only) */
  "advanced-dentistry": {
    label: "Advanced Dentistry", theme: "clinic",
    kind: "Patients overdue for recall",
    note: "Past due for their 6-month hygiene visit — the biggest revenue lever in the practice.",
    moreCount: 16,
    customers: [
      { name: "The Cooper Family", meta: "Family of 4 · last cleaning 8 months ago", channel: "Email", value: 600,
        message: "It's been a while since the Coopers were in for a cleaning 🦷 We've made booking easy — reply or call and we'll find times that work for the whole family. — The team at Advanced Dentistry" },
      { name: "Janet Wu", meta: "Overdue 7 months", channel: "Owner-send SMS", value: 200,
        message: "Hi Janet! You're due for your 6-month cleaning. Reply here and we'll get you on the schedule at a time that's easy. — Advanced Dentistry" },
      { name: "Robert Hale", meta: "Overdue 9 months · usually every 6", channel: "Email", value: 200,
        message: "Hi Robert — we noticed it's been a bit longer than usual since your last visit. Your smile's due for some care; reply and we'll find a slot that works. — The team" },
      { name: "The Diaz Family", meta: "Family · overdue 8 months", channel: "Email", value: 450,
        message: "Time for the Diaz family's cleanings! We can line up back-to-back appointments so it's one easy trip. Reply or call and we'll set it up. — Advanced Dentistry" },
      { name: "Priya Nair", meta: "Overdue 7 months", channel: "Owner-send SMS", value: 200,
        message: "Hi Priya — friendly reminder you're due for a cleaning. We've got openings this and next week; reply and we'll book you in. — Advanced Dentistry" },
      { name: "Greg Olsen", meta: "Overdue 10 months", channel: "Email", value: 200,
        message: "Hi Greg — it's been almost a year since your last visit and we'd love to get you back in. Booking takes one reply. — The team at Advanced Dentistry" },
      { name: "The Bennetts", meta: "Overdue 8 months", channel: "Email", value: 380,
        message: "The Bennetts are due for cleanings! Reply and we'll find a time that works for everyone — we'll even send a reminder the day before. — Advanced Dentistry" },
    ],
  },
};
