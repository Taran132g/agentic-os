/* ============================================================
   In-browser AI voice demo — "The Copper Lantern".

   The brain is now an ElevenLabs restaurant-HOST agent: ElevenLabs owns the
   voice, speech-to-text, the conversational LLM, and the turn-taking. We connect
   over a short-lived signed URL (key stays on the server) and answer the agent's
   two CLIENT tools in the browser:
     • check_availability(day,time,party_size) → "open" | "limited" | "full"
     • book_table(...)  → updates the booking card + the call history, and POSTs
       the reservation to /api/booking → the owner-approval queue.

   Run with:  python3 serve.py   (NOT plain http.server — needs the signed-url
   + booking endpoints, and an ElevenLabs agent provisioned for copper-lantern).
   ============================================================ */

import { Conversation } from "https://cdn.jsdelivr.net/npm/@elevenlabs/client/+esm";

const $ = (s) => document.querySelector(s);

let conversation = null;
let active = false;
let booking = { party: "", day: "", time: "", name: "" };

/* ── availability (mirrors serve.py) — answered locally, zero latency ── */
const AVAILABILITY = {
  Friday:   { "6:00 PM": "open", "6:30 PM": "open", "7:00 PM": "full",
              "7:30 PM": "limited", "8:00 PM": "open", "8:30 PM": "open" },
  Saturday: { "6:00 PM": "limited", "6:30 PM": "full", "7:00 PM": "full",
              "7:30 PM": "full", "8:00 PM": "limited", "8:30 PM": "open" },
  Sunday:   { "11:00 AM": "open", "12:00 PM": "limited", "1:00 PM": "open",
              "6:00 PM": "open", "7:00 PM": "open" },
  _default: { "6:00 PM": "open", "7:00 PM": "open", "8:00 PM": "open", "9:00 PM": "open" },
};
function checkAvailability({ day = "", time = "", party_size = "" }) {
  const size = parseInt(String(party_size).replace(/\D/g, ""), 10) || 0;
  if (size >= 9) return "full"; // 9+ needs the private party room + 48h notice
  const norm = (s) => s.trim().replace(/\s+/g, " ").toUpperCase();
  const dayKey = Object.keys(AVAILABILITY).find((d) => norm(d) === norm(day)) || "_default";
  const slots = AVAILABILITY[dayKey];
  const slotKey = Object.keys(slots).find((t) => norm(t) === norm(time));
  return slotKey ? slots[slotKey] : "open";
}

/* ── past calls the AI handled (seed) — newest first; live calls prepend ── */
const CALLS = [
  { time: "Last night · 9:14pm", caller: "(610) 555-0148", outcome: "booked",
    summary: "Booked · party of 6 · Friday 8:00 PM · Marcus",
    transcript: [
      ["ai", "Thanks for calling The Copper Lantern! How can I help you tonight?"],
      ["you", "Hey, do you have room for six this Friday around seven?"],
      ["ai", "Six on Friday — love it. 7 o'clock is actually full, but I've got 6:30 or 8 open. Either work?"],
      ["you", "Let's do eight."],
      ["ai", "Perfect, 8 PM. And a name for the table?"],
      ["you", "Marcus."],
      ["ai", "You're all set, Marcus — six of you Friday at 8. See you then!"],
    ] },
  { time: "Last night · 8:47pm", caller: "(484) 555-0210", outcome: "info",
    summary: "Answered · hours + dog-friendly patio question",
    transcript: [
      ["ai", "Thanks for calling The Copper Lantern! How can I help you?"],
      ["you", "What time do you close, and is your patio dog friendly?"],
      ["ai", "We're open till 2 AM, kitchen till 11 — and yep, the patio's dog friendly. Bring the pup!"],
      ["you", "Awesome, thanks."],
      ["ai", "Anytime — see you soon!"],
    ] },
  { time: "Yesterday · 7:32pm", caller: "(610) 555-0177", outcome: "callback",
    summary: "Party room · 12 for a birthday · flagged for callback",
    transcript: [
      ["ai", "Thanks for calling The Copper Lantern! How can I help you?"],
      ["you", "I want to do a birthday for about twelve people next weekend."],
      ["ai", "Twelve's perfect for our party room! That one needs about 48 hours' notice — I'll have a manager call you to lock the date. What's the best number?"],
      ["you", "This number's fine, six one zero, five five five, oh one seven seven."],
      ["ai", "Got it — someone will call you today to set it up. Thanks!"],
    ] },
  { time: "Yesterday · 6:50pm", caller: "(267) 555-0193", outcome: "booked",
    summary: "Booked · party of 2 · Saturday 8:30 PM · Priya",
    transcript: [
      ["ai", "Thanks for calling The Copper Lantern! How can I help you tonight?"],
      ["you", "Table for two on Saturday, maybe eight-ish?"],
      ["ai", "Saturday's busy — 8 is full, but 8:30 is open. Want that?"],
      ["you", "Sure, that works. It's Priya."],
      ["ai", "Done, Priya — two of you Saturday at 8:30. See you then!"],
    ] },
];

/* ── UI helpers ── */
function addBubble(role, text) {
  const li = document.createElement("li");
  li.className = `cl-msg ${role}`;
  li.innerHTML = `<span class="cl-who">${role === "ai" ? "Copper Lantern" : "You"}</span>${text}`;
  $("#callLog").appendChild(li);
  $("#callLog").scrollTop = $("#callLog").scrollHeight;
  return li;
}
function setStatus(badge, sub) {
  $("#callBadge").textContent = badge;
  if (sub) $("#callStatus").textContent = sub;
}
function updateBooking(b) {
  const has = Object.values(b).some((v) => v);
  if (has) $("#bookingCard").hidden = false;
  if (b.party) $("#bkParty").textContent = b.party;
  if (b.day)   $("#bkDate").textContent  = b.day;
  if (b.time)  $("#bkTime").textContent  = b.time;
  if (b.name)  $("#bkName").textContent  = b.name;
}

const OUTCOME = { booked: "Booked ✓", info: "Answered", callback: "Callback" };
function renderCalls() {
  $("#callsList").innerHTML = CALLS.map((c, i) => `
    <li class="call-row" data-i="${i}">
      <div class="cr-head">
        <span class="cr-badge ${c.outcome}">${OUTCOME[c.outcome] || "Answered"}</span>
        <span class="cr-caller">${c.caller}</span>
        <span class="cr-time">${c.time}</span>
        <span class="cr-chev">›</span>
      </div>
      <div class="cr-summary">${c.summary}</div>
      <div class="cr-transcript" hidden>${c.transcript.map(([r, t]) =>
        `<div class="crt ${r}"><span>${r === "ai" ? "AI" : "Caller"}</span>${t}</div>`).join("")}</div>
    </li>`).join("");
}

/* ── client tool: book_table ── */
async function bookTable(args) {
  booking = {
    party: args.party_size || booking.party,
    day: args.day || booking.day,
    time: args.time || booking.time,
    name: args.name || booking.name,
  };
  updateBooking(booking);
  $("#bkStatus").textContent = "Booked ✓ — added to the book";
  $("#bkStatus").classList.add("booked");
  setStatus("Booked ✓", "Reservation captured");

  CALLS.unshift({
    time: "Just now · live demo", caller: "Demo call", outcome: "booked",
    summary: `Booked · ${booking.party || "?"} · ${booking.day || "?"} ${booking.time || ""} · ${booking.name || "?"}`,
    transcript: [...$("#callLog").querySelectorAll(".cl-msg")].map((li) => [
      li.classList.contains("ai") ? "ai" : "you",
      li.textContent.replace(/^(Copper Lantern|You)/, ""),
    ]),
  });
  renderCalls();

  try {
    await fetch("/api/booking", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(args),
    });
  } catch (e) { /* demo UI already updated; owner queue is best-effort */ }
  return "Booked and added to the reservation book.";
}

/* ── call lifecycle ── */
async function startCall() {
  setStatus("Connecting", "Asking for your microphone…");
  $("#callBtn").disabled = true;
  let signed = {}, code = 0;
  try {
    const res = await fetch("/api/voice/signed-url");
    code = res.status;
    signed = await res.json();
  } catch (e) {
    signed = { error: "Could not reach the demo." };
  }
  if (!signed.signed_url) {
    $("#callBtn").disabled = false;
    if (code === 429) {
      setStatus("One sec", "Line's busy");
      addBubble("ai", "Give the line a few seconds, then tap call again.");
    } else if (code === 503) {
      setStatus("Demo busy", "Free minutes used up");
      addBubble("ai", "Our live demo line is maxed out for now — you can "
        + "<a href='https://cal.com/taranveer/workflows' target='_blank'>book a quick call</a> "
        + "and we'll walk you through the whole thing.");
    } else {
      setStatus("Unavailable", "Try again shortly");
      addBubble("ai", "The voice line isn't available right now — please try again soon.");
    }
    return;
  }

  try {
    conversation = await Conversation.startSession({
      signedUrl: signed.signed_url,
      clientTools: {
        check_availability: (args) => checkAvailability(args || {}),
        book_table: (args) => bookTable(args || {}),
      },
      onConnect: () => {
        active = true;
        $("#callLog").innerHTML = "";
        $("#bookingCard").hidden = true;
        ["#bkParty", "#bkDate", "#bkTime", "#bkName"].forEach((id) => ($(id).textContent = "—"));
        $("#bkStatus").textContent = "Taking your reservation…";
        $("#bkStatus").classList.remove("booked");
        booking = { party: "", day: "", time: "", name: "" };
        $("#callBtn").disabled = false;
        $("#callBtn").textContent = "End call";
        $("#callBtn").classList.replace("start", "end");
        $("#callOrb").classList.add("ringing");
        setStatus("In call", "Connected — start talking");
      },
      onDisconnect: () => endCall(true),
      onError: (err) => { addBubble("ai", "Connection hiccup — try calling again."); console.error(err); },
      onModeChange: ({ mode }) => {
        if (!active) return;
        $("#callOrb").classList.toggle("speaking", mode === "speaking");
        setStatus("In call", mode === "speaking" ? "The host is speaking…" : "Listening…");
      },
      onMessage: ({ message, source }) => {
        if (message) addBubble(source === "user" ? "you" : "ai", message);
      },
    });
  } catch (e) {
    $("#callBtn").disabled = false;
    setStatus("Idle", "Mic blocked or connection failed");
    addBubble("ai", "I couldn't start the call — check microphone permission and try again.");
    console.error(e);
  }
}

async function endCall(silent) {
  active = false;
  if (conversation) { try { await conversation.endSession(); } catch (e) {} conversation = null; }
  $("#callBtn").disabled = false;
  $("#callBtn").textContent = "📞 Call again";
  $("#callBtn").classList.replace("end", "start");
  $("#callOrb").classList.remove("ringing", "speaking");
  if (!silent) setStatus("Idle", "Tap to call — the AI will answer");
}

/* ── wire-up ── */
document.addEventListener("DOMContentLoaded", () => {
  $("#answerRow")?.setAttribute("hidden", "");   // voice-first: no typed input
  $("#callBtn").addEventListener("click", () => (active ? endCall() : startCall()));
  $("#callsList").addEventListener("click", (e) => {
    const row = e.target.closest(".call-row");
    if (!row) return;
    const t = row.querySelector(".cr-transcript");
    t.hidden = !t.hidden;
    row.classList.toggle("open", !t.hidden);
  });
  renderCalls();
});
