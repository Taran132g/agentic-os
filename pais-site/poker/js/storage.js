// play/js/storage.js — Session and hand history persistence via localStorage.

const SESSION_KEY = "pais_poker_session_v1";
const HISTORY_KEY = "pais_poker_history_v1";
const MAX_HISTORY = 100;

export function loadSession() {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw);
    if (s?.version !== 1) return null;
    return s;
  } catch {
    return null;
  }
}

export function saveSession(s) {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(s));
  } catch {}
}

export function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const h = JSON.parse(raw);
    return Array.isArray(h) ? h : [];
  } catch {
    return [];
  }
}

export function appendHistory(record) {
  try {
    const h = loadHistory();
    h.push(record);
    if (h.length > MAX_HISTORY) h.splice(0, h.length - MAX_HISTORY);
    localStorage.setItem(HISTORY_KEY, JSON.stringify(h));
  } catch {}
}

export function clearAll() {
  try {
    localStorage.removeItem(SESSION_KEY);
    localStorage.removeItem(HISTORY_KEY);
  } catch {}
}
