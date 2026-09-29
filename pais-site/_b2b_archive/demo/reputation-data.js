/* ============================================================
   Reputation queue per business — what the Reputation agent drafted
   overnight. Two jobs, surfaced as one approval queue:
     • replies — owner-voiced answers to new Google reviews
                  (angry ones get a warm, specific, non-defensive fix)
     • asks    — short review requests to last night's happy guests
   Plugs the universal leak: unanswered reviews + thin volume drag
   down "near me" rank. Owner approves; nothing posts on its own.
   ============================================================ */

const REPUTATION = {
  /* Trappe Tavern — busy Friday left a couple of bruises + a lot of fans */
  "trappe-tavern": {
    label: "Trappe Tavern", theme: "tavern",
    rating: "4.6", reviewCount: 212,
    note: "New Google reviews since yesterday + last night's 5★ visits — drafted in your voice, ready to post.",
    moreCount: 0,
    replies: [
      { author: "Greg M.", stars: 2, when: "2h ago",
        review: "Came in Friday around 9, waited 25 minutes just to get a drink. Food was good once it came but the wait killed it.",
        channel: "Post as Google reply",
        draft: "Greg — you're right, Friday after 9 got away from us and a 25-minute wait isn't the night we want to give you. We've added a second person behind the bar for the late rush so it won't happen again. Come back in and let me make it right — first round's on me. — Tom, owner" },
      { author: "Dana R.", stars: 5, when: "5h ago",
        review: "Best wings in the area, hands down. Tom and the staff always make us feel like regulars. Our Friday spot for good.",
        channel: "Post as Google reply",
        draft: "Dana — this made our whole week 🍺 The Friday crew wouldn't be the same without you. See you this week — wings are on standby. — Tom" },
      { author: "Mike P.", stars: 3, when: "yesterday",
        review: "Solid food and great beer list, but my wings came out lukewarm. Server was friendly about it though.",
        channel: "Post as Google reply",
        draft: "Mike — lukewarm wings are on us, not you, and I appreciate you flagging it. We've tightened how we time the kitchen on a busy bar night. Next basket's hot and on the house — just ask for Tom. Thanks for giving us another look." },
    ],
    asks: [
      { count: 6, when: "last night", channel: "Owner-send SMS", value: 0,
        label: "6 standout-visit guests",
        draft: "Hey — great having you in last night! If you've got 20 seconds, a quick Google review really helps a small spot like ours: [review link]. Thanks for being a regular. — Trappe Tavern" },
    ],
  },

  /* TruFit — billing gripe + a glowing trainer shout-out + PR crowd */
  "trufit": {
    label: "TruFit", theme: "fitness",
    rating: "4.8", reviewCount: 168,
    note: "New Google reviews + members who crushed a session yesterday — drafted for Mara to approve.",
    moreCount: 3,
    replies: [
      { author: "Steph L.", stars: 2, when: "4h ago",
        review: "Love the classes but getting charged after I tried to cancel was frustrating. Took two emails to sort out.",
        channel: "Post as Google reply",
        draft: "Steph — I'm sorry the cancellation took two tries; that's on us and not the experience we want you to have. I've changed how we handle cancel requests so they're confirmed same-day in writing. If anything's still off on your account, email me directly and I'll fix it today. — Mara" },
      { author: "Andre W.", stars: 5, when: "yesterday",
        review: "Coach Jay completely changed how I train. Down 22 lbs and actually look forward to the 6am bootcamp now.",
        channel: "Post as Google reply",
        draft: "Andre — 22 lbs and showing up at 6am?! That's all you. I'll make sure Jay sees this 💪 Proud of you — keep going. — Mara" },
    ],
    asks: [
      { count: 9, when: "yesterday", channel: "Owner-send SMS", value: 0,
        label: "9 members who PR'd or hit a streak",
        draft: "Huge session yesterday 💪 If TruFit's been good to you, a quick Google review helps another person take the first step: [review link]. Appreciate you. — Mara" },
    ],
  },

  /* Advanced Dentistry — wait-time gripe (kept HIPAA-safe), 5★ fan, recall fans */
  "advanced-dentistry": {
    label: "Advanced Dentistry", theme: "clinic",
    rating: "4.9", reviewCount: 341,
    note: "New Google reviews + patients who had a smooth visit — drafted to stay warm and HIPAA-safe (no patient details).",
    moreCount: 2,
    replies: [
      { author: "J. Hartman", stars: 2, when: "6h ago",
        review: "Staff is nice but I waited almost 40 minutes past my appointment time. If I'm late they charge me, so it goes both ways.",
        channel: "Post as Google reply",
        draft: "Thank you for the honest feedback — a 40-minute wait isn't acceptable and you're right that it should go both ways. We've adjusted how we build the schedule to leave room for longer visits so the day doesn't slip. We'd genuinely like the chance to do better; please give the office a call and ask for the manager. — Advanced Dentistry" },
      { author: "Maria S.", stars: 5, when: "yesterday",
        review: "Gentlest cleaning I've ever had and they explained everything without making me feel rushed. Whole family comes here now.",
        channel: "Post as Google reply",
        draft: "Maria — thank you so much, this means a lot to the whole team 🦷 We're glad the family feels at home here. See you all at the next visit! — Advanced Dentistry" },
    ],
    asks: [
      { count: 5, when: "yesterday", channel: "Owner-send SMS", value: 0,
        label: "5 patients with a smooth visit",
        draft: "Thanks for coming in! If you have a moment, a quick Google review helps other families find a dentist they can trust: [review link]. We appreciate you. — Advanced Dentistry" },
    ],
  },
};
