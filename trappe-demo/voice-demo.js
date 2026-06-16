/* ============================================================
   In-browser AI voice demo — "The Copper Lantern".
   The AI brain is a REAL LLM: each turn POSTs the conversation to
   /api/voice (served by serve.py → claude -p Haiku) and gets back
   {say, booking, done}. The AI actually understands the caller —
   no client-side keyword matching. UI = speech out + mic/typed in.
   Run with:  python3 serve.py   (NOT plain http.server)
   ============================================================ */

const $ = (s) => document.querySelector(s);

const GREETING = "Thanks for calling The Copper Lantern! How can I help you tonight?";
const STARTERS = ["I'd like to book a table for 2", "Do you have a party room?", "What are your hours?"];

let messages = [];          // [{role:'user'|'assistant', content}]
let active = false;

/* Past calls the AI handled (seed) — newest first. Live demo calls prepend here. */
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

/* ---------------- speech out ---------------- */
let voice = null;
function loadVoice() {
  const vs = speechSynthesis.getVoices();
  voice = vs.find(v => /Samantha|Google US English|Jenny|Aria/i.test(v.name) && /en/i.test(v.lang))
       || vs.find(v => /en-US/i.test(v.lang)) || vs[0] || null;
}
if ("speechSynthesis" in window) { loadVoice(); speechSynthesis.onvoiceschanged = loadVoice; }
function speak(text) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  if (voice) u.voice = voice;
  u.rate = 1.02;
  speechSynthesis.speak(u);
}

/* ---------------- UI helpers ---------------- */
function addBubble(role, text) {
  const li = document.createElement("li");
  li.className = `cl-msg ${role}`;
  li.innerHTML = `<span class="cl-who">${role === "ai" ? "Copper Lantern" : "You"}</span>${text}`;
  $("#callLog").appendChild(li);
  $("#callLog").scrollTop = $("#callLog").scrollHeight;
  return li;
}
function thinkingBubble() {
  const li = addBubble("ai", `<span class="dots"><i></i><i></i><i></i></span>`);
  li.classList.add("thinking");
  return li;
}
function setChips(arr) {
  $("#chips").innerHTML = (arr || []).map(c => `<button class="chip-ans">${c}</button>`).join("");
}
function setStatus(badge, sub) {
  $("#callBadge").textContent = badge;
  if (sub) $("#callStatus").textContent = sub;
}
function updateBooking(b) {
  if (!b) return;
  const has = Object.values(b).some(v => v);
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

/* ---------------- call lifecycle ---------------- */
function startCall() {
  active = true; messages = [];
  $("#callLog").innerHTML = "";
  $("#bookingCard").hidden = true;
  ["#bkParty", "#bkDate", "#bkTime", "#bkName"].forEach(id => $(id).textContent = "—");
  $("#bkStatus").textContent = "Taking your reservation…"; $("#bkStatus").classList.remove("booked");
  $("#callBtn").textContent = "End call"; $("#callBtn").classList.replace("start", "end");
  $("#callOrb").classList.add("ringing"); setStatus("In call", "Connected — the AI is answering…");
  $("#answerRow").hidden = false;
  if (recog) $("#micBtn").hidden = false;
  $("#textInput").focus();

  messages.push({ role: "assistant", content: GREETING });
  addBubble("ai", GREETING);
  setChips(STARTERS);
  speak(GREETING);
}

function endCall(silent) {
  active = false;
  $("#callBtn").textContent = "📞 Call now"; $("#callBtn").classList.replace("end", "start");
  $("#callOrb").classList.remove("ringing");
  $("#answerRow").hidden = true; $("#micBtn").hidden = true; setChips([]);
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  if (recog) { try { recog.stop(); } catch (e) {} }
  if (!silent) setStatus("Idle", "Tap to call — the AI will answer");
}

async function handleInput(text) {
  if (!active || !text.trim()) return;
  addBubble("you", text);
  setChips([]);
  messages.push({ role: "user", content: text });

  const thinking = thinkingBubble();
  setStatus("In call", "The AI is thinking…");
  let data;
  try {
    const res = await fetch("/api/voice", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    data = await res.json();
  } catch (e) {
    data = { say: "Sorry — I lost you there. Could you say that again?", booking: {}, done: false };
  }
  thinking.remove();

  const say = data.say || "Sorry, could you repeat that?";
  messages.push({ role: "assistant", content: say });
  addBubble("ai", say);
  updateBooking(data.booking);
  speak(say);
  setStatus("In call", "Connected");

  if (data.done) {
    $("#bkStatus").textContent = "Booked ✓ — added to the book";
    $("#bkStatus").classList.add("booked");
    setStatus("Booked ✓", "Reservation captured");
    active = false;
    $("#answerRow").hidden = true; $("#micBtn").hidden = true;
    $("#callBtn").textContent = "📞 Call again"; $("#callBtn").classList.replace("end", "start");
    $("#callOrb").classList.remove("ringing");
    // log this live call into the history the restaurant sees
    const b = { party: $("#bkParty").textContent, day: $("#bkDate").textContent,
                time: $("#bkTime").textContent, name: $("#bkName").textContent };
    CALLS.unshift({
      time: "Just now · live demo", caller: "Demo call", outcome: "booked",
      summary: `Booked · ${b.party} · ${b.day} ${b.time} · ${b.name}`.replace(/—/g, "?"),
      transcript: messages.map(m => [m.role === "assistant" ? "ai" : "you", m.content]),
    });
    renderCalls();
  } else if (recog) {
    $("#micBtn").hidden = false;
  }
}

/* ---------------- mic (optional) ---------------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recog = null, listening = false;
if (SR) {
  recog = new SR();
  recog.lang = "en-US"; recog.interimResults = false; recog.maxAlternatives = 1;
  recog.onresult = (e) => { const t = e.results[0][0].transcript; listening = false; setMic(false); handleInput(t); };
  recog.onerror = () => { listening = false; setMic(false); };
  recog.onend = () => { listening = false; setMic(false); };
}
function setMic(on) { const b = $("#micBtn"); if (b) b.classList.toggle("listening", on); }
function toggleMic() {
  if (!recog) return;
  if (listening) { try { recog.stop(); } catch (e) {} return; }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  try { recog.start(); listening = true; setMic(true); } catch (e) {}
}

/* ---------------- wire-up ---------------- */
document.addEventListener("DOMContentLoaded", () => {
  $("#callBtn").addEventListener("click", () => { active ? endCall() : startCall(); });
  $("#micBtn").addEventListener("click", toggleMic);
  $("#sendBtn").addEventListener("click", () => { const v = $("#textInput").value; $("#textInput").value = ""; handleInput(v); });
  $("#textInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { const v = $("#textInput").value; $("#textInput").value = ""; handleInput(v); } });
  $("#chips").addEventListener("click", (e) => { const c = e.target.closest(".chip-ans"); if (c) handleInput(c.textContent); });
  $("#callsList").addEventListener("click", (e) => {
    const row = e.target.closest(".call-row"); if (!row) return;
    const t = row.querySelector(".cr-transcript");
    t.hidden = !t.hidden; row.classList.toggle("open", !t.hidden);
  });
  renderCalls();
  if (!SR) $("#micBtn")?.setAttribute("hidden", "");
});
