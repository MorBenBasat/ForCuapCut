/** ForCuapCut web UI */

const state = {
  lastSong: null,
  artists: [],
  chords: [],
  thumbPreviewTimer: null,
  thumbPreviewBusy: false,
  strumPreviewTimer: null,
  strumPreviewBusy: false,
  strumPattern: ["D", "D", "U", "U", "D", "U"],
};

const STRUM_MIN_BEATS = 2;
const STRUM_MAX_BEATS = 16;
const STRUM_SYMBOLS = { D: "↓", U: "↑", X: "×" };

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 4000);
}

function showResult(containerId, html, ok = true) {
  const el = document.getElementById(containerId);
  el.innerHTML = html;
  el.classList.remove("hidden", "success", "error");
  el.classList.add(ok ? "success" : "error");
}

function buildChordInputs(count, chords = []) {
  const wrap = $("#chord-inputs");
  const existing = [...wrap.querySelectorAll("[data-chord]")].map((el) => el.value);
  const picked = chords.length ? chords : existing;
  wrap.innerHTML = "";
  for (let i = 1; i <= count; i++) {
    const row = document.createElement("div");
    row.className = "chord-row";
    row.innerHTML = `
      <span class="chord-num">${i}</span>
      <select data-chord="${i}" required>
        ${chordSelectOptions(picked[i - 1] || "")}
      </select>
    `;
    wrap.appendChild(row);
  }
}

function getSongChords() {
  const selects = $$("#chord-inputs [data-chord]");
  return [...selects].map((sel) => sel.value.trim()).filter(Boolean);
}

function chordSelectOptions(selected = "") {
  const blank = `<option value="">בחר אקורד…</option>`;
  const opts = state.chords
    .map(
      (name) =>
        `<option value="${escapeAttr(name)}"${name === selected ? " selected" : ""}>${escapeHtml(name)}</option>`
    )
    .join("");
  return blank + opts;
}

function getVideoChordCount() {
  return Number($("#video-chord-count").value);
}

function getVideoChords() {
  const selects = $$("#video-chord-rows select[data-video-chord]");
  return [...selects].map((sel) => sel.value.trim()).filter(Boolean);
}

function buildVideoTimingRows(chords = []) {
  const wrap = $("#video-chord-rows");
  const count = getVideoChordCount();
  const existingTimes = [...wrap.querySelectorAll("[data-time]")].map((inp) => inp.value);
  const existingChords = [...wrap.querySelectorAll("[data-video-chord]")].map((sel) => sel.value);
  wrap.innerHTML = "";

  for (let i = 0; i < count; i++) {
    const num = i + 1;
    const defaultTime = existingTimes[i] ?? i * 4;
    const chord = chords[i] ?? existingChords[i] ?? "";
    const row = document.createElement("div");
    row.className = "timing-row";
    row.innerHTML = `
      <span class="chord-num">${num}</span>
      <select data-video-chord="${num}" required>
        ${chordSelectOptions(chord)}
      </select>
      <input type="number" name="time-${num}" value="${defaultTime}" min="0" step="0.5"
        ${num === 1 ? "disabled title='תמיד 0'" : ""} data-time="${num}">
    `;
    wrap.appendChild(row);
  }
}

function syncThumbnailFromSong() {
  if (!state.lastSong) return;
  const song = state.lastSong.song;
  const artist = state.lastSong.artist;
  if (!$("#thumb-song").value.trim()) $("#thumb-song").value = song;
  if (!$("#thumb-line1").value.trim()) $("#thumb-line1").value = "איך לנגן";
  if (!$("#thumb-line2").value.trim()) $("#thumb-line2").value = `"${song}"`;
  if (!$("#thumb-line3").value.trim()) $("#thumb-line3").value = `של ${artist}`;
  // רמת קושי נשארת לבחירת המשתמש (ברירת מחדל: ללא רמה)
}

function getThumbnailFormData() {
  const form = $("#form-thumbnail");
  const fd = new FormData(form);
  return fd;
}

function thumbnailLinesReady() {
  return (
    $("#thumb-line1").value.trim() &&
    $("#thumb-line2").value.trim() &&
    $("#thumb-line3").value.trim()
  );
}

function showThumbnailPreview(data) {
  const wrap = $("#thumb-preview-wrap");
  const img = $("#thumb-preview-img");
  const note = $("#thumb-preview-note");
  wrap.classList.remove("hidden");
  img.src = `${data.file}?t=${Date.now()}`;
  note.textContent = data.used_custom_background
    ? data.has_difficulty
      ? "רקע: התמונה שהעלית · עם תג רמה"
      : "רקע: התמונה שהעלית · פס צהוב (ללא תג רמה)"
    : data.has_difficulty
      ? "רקע: גיטרה · עם תג רמה"
      : "רקע: גיטרה · פס צהוב (ללא תג רמה)";
}

async function refreshThumbnailPreview({ quiet = false } = {}) {
  if (!thumbnailLinesReady()) {
    if (!quiet) toast("מלא לפחות 3 שורות טקסט", true);
    return;
  }
  if (state.thumbPreviewBusy) return;

  state.thumbPreviewBusy = true;
  const btn = $("#thumb-preview-btn");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/api/thumbnail/preview", {
      method: "POST",
      body: getThumbnailFormData(),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error);
    showThumbnailPreview(data);
    if (!quiet) toast("תצוגה מקדימה עודכנה");
  } catch (err) {
    if (!quiet) toast(err.message, true);
  } finally {
    state.thumbPreviewBusy = false;
    if (btn) btn.disabled = false;
  }
}

function scheduleThumbnailPreview() {
  clearTimeout(state.thumbPreviewTimer);
  state.thumbPreviewTimer = setTimeout(() => {
    if (thumbnailLinesReady()) {
      refreshThumbnailPreview({ quiet: true });
    }
  }, 700);
}

function buildStrumBeats(pattern = state.strumPattern) {
  state.strumPattern = [...pattern];
  const wrap = $("#strum-beats");
  if (!wrap) return;

  wrap.innerHTML = "";
  pattern.forEach((value, index) => {
    const row = document.createElement("div");
    row.className = "strum-beat-row";
    row.dataset.beat = String(index);

    const num = document.createElement("span");
    num.className = "strum-beat-num";
    num.textContent = String(index + 1);

    const options = document.createElement("div");
    options.className = "strum-beat-options";

    ["D", "U", "X"].forEach((token) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `strum-opt${value === token ? " active" : ""}`;
      btn.dataset.value = token;
      btn.title = token === "D" ? "למטה" : token === "U" ? "למעלה" : "השתקה";
      btn.textContent = STRUM_SYMBOLS[token];
      btn.addEventListener("click", () => {
        state.strumPattern[index] = token;
        buildStrumBeats(state.strumPattern);
        updateStrumLivePreview();
        scheduleStrumPreview();
      });
      options.appendChild(btn);
    });

    row.appendChild(num);
    row.appendChild(options);
    wrap.appendChild(row);
  });

  const label = $("#strum-beat-count-label");
  if (label) label.textContent = `${pattern.length} פעימות`;
  updateStrumLivePreview();
}

function getStrumPattern() {
  return [...state.strumPattern];
}

function updateStrumLivePreview() {
  const el = $("#strum-live-preview");
  if (!el) return;
  const parts = [];
  getStrumPattern().forEach((token, index) => {
    if (index > 0) {
      parts.push('<span class="strum-live-connector">●</span>');
    }
    parts.push(`<span class="strum-live-chip ${token}">${STRUM_SYMBOLS[token]}</span>`);
  });
  el.innerHTML = parts.join("");
}

function syncStrumFromSong() {
  if (!state.lastSong) return;
  const song = state.lastSong.song;
  const artist = state.lastSong.artist;
  if ($("#strum-song") && !$("#strum-song").value.trim()) $("#strum-song").value = song;
  if ($("#strum-subtitle") && !$("#strum-subtitle").value.trim()) {
    $("#strum-subtitle").value = `${song} — ${artist}`;
  }
}

function getStrumFormData() {
  const fd = new FormData($("#form-strum"));
  fd.set("pattern", JSON.stringify(getStrumPattern()));
  return fd;
}

function showStrumPreview(data) {
  const wrap = $("#strum-preview-wrap");
  const img = $("#strum-preview-img");
  const note = $("#strum-preview-note");
  if (!wrap || !img || !note) return;
  wrap.classList.remove("hidden");
  img.src = `${data.file}?t=${Date.now()}`;
  note.textContent = data.used_custom_background
    ? `רקע: התמונה שהעלית · ${data.beat_count} פעימות`
    : `רקע: גיטרה · ${data.beat_count} פעימות`;
}

async function refreshStrumPreview({ quiet = false } = {}) {
  if (getStrumPattern().length < STRUM_MIN_BEATS) {
    if (!quiet) toast(`צריך לפחות ${STRUM_MIN_BEATS} פעימות`, true);
    return;
  }
  if (state.strumPreviewBusy) return;

  state.strumPreviewBusy = true;
  const btn = $("#strum-preview-btn");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/api/strum/preview", {
      method: "POST",
      body: getStrumFormData(),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error);
    showStrumPreview(data);
    if (!quiet) toast("תצוגה מקדימה עודכנה");
  } catch (err) {
    if (!quiet) toast(err.message, true);
  } finally {
    state.strumPreviewBusy = false;
    if (btn) btn.disabled = false;
  }
}

function scheduleStrumPreview() {
  clearTimeout(state.strumPreviewTimer);
  state.strumPreviewTimer = setTimeout(() => {
    if (getStrumPattern().length >= STRUM_MIN_BEATS) {
      refreshStrumPreview({ quiet: true });
    }
  }, 700);
}

function syncVideoFromSong() {
  if (!state.lastSong) return;
  $("#video-artist").value = state.lastSong.artist;
  $("#video-title").value = state.lastSong.song;
  if (state.lastSong.difficulty) {
    $("#video-difficulty").value = state.lastSong.difficulty;
  }
  const count = state.lastSong.chordNames.length;
  if (count >= 4 && count <= 8) {
    $("#video-chord-count").value = String(count);
  }
  buildVideoTimingRows(state.lastSong.chordNames);
}

function setVideoMetaLocked(locked) {
  $("#video-meta-fields").classList.toggle("locked", locked);
}

function fillArtistSelects() {
  const opts = state.artists
    .map((a) => `<option value="${escapeAttr(a)}">${escapeHtml(a)}</option>`)
    .join("");
  $("#song-artist").innerHTML = `<option value="">בחר זמר…</option>${opts}`;
  $("#video-artist").innerHTML = `<option value="">בחר זמר…</option>${opts}`;
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
}

function escapeAttr(s) {
  return escapeHtml(s);
}

async function loadArtists() {
  const res = await fetch("/api/artists");
  const data = await res.json();
  state.artists = data.artists || [];
  fillArtistSelects();
  renderArtistsList();
}

async function loadChords() {
  const res = await fetch("/api/chords");
  const data = await res.json();
  state.chords = data.chords || [];
  buildChordInputs(Number($("#chord-count").value));
  buildVideoTimingRows();
}

async function loadSession() {
  const res = await fetch("/api/session");
  const data = await res.json();
  if (data.difficulty) {
    $("#song-difficulty").value = data.difficulty;
    $("#video-difficulty").value = data.difficulty;
    const introDiff = document.querySelector("#form-intro select[name=difficulty]");
    if (introDiff) introDiff.value = data.difficulty;
    const thumbDiff = $("#thumb-difficulty");
    if (thumbDiff && thumbDiff.value && data.difficulty) {
      thumbDiff.value = data.difficulty;
    }
  }
}

function renderArtistsList() {
  const ul = $("#artists-list");
  if (!state.artists.length) {
    ul.innerHTML = "<li class='hint'>אין זמרים — הוסף למטה</li>";
    return;
  }
  ul.innerHTML = state.artists
    .map(
      (name) => `
    <li>
      <span>${escapeHtml(name)}</span>
      <button type="button" class="btn danger" data-delete="${escapeAttr(name)}">מחק</button>
    </li>`
    )
    .join("");

  ul.querySelectorAll("[data-delete]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm(`למחוק את ${btn.dataset.delete}?`)) return;
      const res = await fetch("/api/artists", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: btn.dataset.delete }),
      });
      const data = await res.json();
      if (data.ok) {
        toast("נמחק");
        await loadArtists();
      } else {
        toast(data.error || "שגיאה", true);
      }
    });
  });
}

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`panel-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "video" && $("#video-use-song").checked) {
      syncVideoFromSong();
    }
    if (tab.dataset.tab === "thumbnail") {
      syncThumbnailFromSong();
    }
    if (tab.dataset.tab === "strum") {
      syncStrumFromSong();
    }
  });
});

$("#chord-count").addEventListener("change", (e) => {
  buildChordInputs(Number(e.target.value), getSongChords());
});

$("#video-chord-count").addEventListener("change", () => {
  buildVideoTimingRows(getVideoChords());
});

$("#video-use-song").addEventListener("change", (e) => {
  setVideoMetaLocked(e.target.checked);
  if (e.target.checked) syncVideoFromSong();
});

$("#song-background")?.addEventListener("change", (e) => {
  const file = e.target.files[0];
  const hint = $("#song-upload-hint");
  if (!hint) return;
  if (file) {
    hint.textContent = `נבחר: ${file.name}`;
    hint.classList.remove("hidden");
  } else {
    hint.classList.add("hidden");
    hint.textContent = "";
  }
});

$("#form-song").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;
  const chords = getSongChords();
  const count = Number($("#chord-count").value);
  if (chords.length !== count) {
    toast(`בחר ${count} אקורדים`, true);
    btn.disabled = false;
    return;
  }

  const fd = new FormData($("#form-song"));
  fd.set("chords", JSON.stringify(chords));

  try {
    const res = await fetch("/api/slide", {
      method: "POST",
      body: fd,
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error);

    state.lastSong = {
      artist: data.artist,
      song: data.song,
      chords: data.chords_numbered,
      difficulty: data.difficulty,
      chordNames: chords,
    };

    const bgNote = data.used_custom_background ? "רקע: התמונה שהעלית" : "רקע: גיטרה";
    showResult(
      "result-song",
      `<p>נוצר: <strong>${escapeHtml(data.filename)}</strong> · ${escapeHtml(bgNote)}</p>
       <p>נשמר בשולחן העבודה: <strong>${escapeHtml(data.desktop_filename)}</strong></p>
       <a href="${data.file}" download>הורד תמונה</a>
       <br><img src="${data.file}?t=${Date.now()}" alt="סלייד">`
    );
    toast("התמונה מוכנה — נשמרה בשולחן העבודה");
    syncVideoFromSong();
  } catch (err) {
    showResult("result-song", `<p>${escapeHtml(err.message)}</p>`, false);
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

$("#form-video").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;
  toast("מייצר סרטון… זה לוקח זמן");

  const chordCount = getVideoChordCount();
  const chords = getVideoChords();
  if (chords.length !== chordCount) {
    toast(`בחר ${chordCount} אקורדים`, true);
    btn.disabled = false;
    return;
  }

  let artist, song, difficulty;
  if ($("#video-use-song").checked && state.lastSong) {
    artist = state.lastSong.artist;
    song = state.lastSong.song;
    difficulty = state.lastSong.difficulty;
  } else {
    if ($("#video-use-song").checked && !state.lastSong) {
      toast("בטל את הסימון או צור קודם סלייד בטאב שיר", true);
      btn.disabled = false;
      return;
    }
    artist = $("#video-artist").value;
    song = $("#video-title").value.trim();
    difficulty = $("#video-difficulty").value;
    if (!artist || !song) {
      toast("חסר זמר או שם שיר", true);
      btn.disabled = false;
      return;
    }
  }

  const timeInputs = [...$("#video-chord-rows").querySelectorAll("[data-time]")];
  const times = timeInputs.map((inp, i) => (i === 0 ? 0 : parseFloat(inp.value)));

  const fd = new FormData();
  fd.append("artist", artist);
  fd.append("song", song);
  fd.append("difficulty", difficulty || "קל");
  fd.append("chords", JSON.stringify(chords));
  fd.append("times", JSON.stringify(times));
  const endVal = $("#video-end").value.trim();
  if (endVal) fd.append("end", endVal);
  fd.append("audio", e.target.audio.files[0]);

  try {
    const res = await fetch("/api/video", { method: "POST", body: fd });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error);

    showResult(
      "result-video",
      `<p>נוצר: <strong>${escapeHtml(data.filename)}</strong></p>
       <a href="${data.file}" download>הורד סרטון</a>
       <br><video src="${data.file}" controls></video>`
    );
    toast("הסרטון מוכן!");
  } catch (err) {
    showResult("result-video", `<p>${escapeHtml(err.message)}</p>`, false);
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

$("#form-intro").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const lines = [fd.get("line1"), fd.get("line2"), fd.get("line3"), fd.get("line4")].filter(
    (l) => l && String(l).trim()
  );
  const res = await fetch("/api/intro", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lines, difficulty: fd.get("difficulty") }),
  });
  const data = await res.json();
  if (data.ok) {
    showResult(
      "result-intro",
      `<p>נוצר: ${escapeHtml(data.filename)}</p>
       <a href="${data.file}" download>הורד</a>
       <br><img src="${data.file}?t=${Date.now()}" alt="פתיחה">`
    );
    toast("פתיחה מוכנה");
  } else {
    showResult("result-intro", `<p>${escapeHtml(data.error)}</p>`, false);
  }
});

$("#form-thumbnail").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;

  try {
    const res = await fetch("/api/thumbnail", {
      method: "POST",
      body: getThumbnailFormData(),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error);

    showThumbnailPreview(data);
    showResult(
      "result-thumbnail",
      `<p>נוצר: <strong>${escapeHtml(data.filename)}</strong></p>
       <p>נשמר בשולחן העבודה: <strong>${escapeHtml(data.desktop_filename)}</strong></p>
       <a href="${data.file}" download>הורד תמונה</a>
       <br><img src="${data.file}?t=${Date.now()}" alt="Thumbnail">`
    );
    toast("Thumbnail מוכן");
  } catch (err) {
    showResult("result-thumbnail", `<p>${escapeHtml(err.message)}</p>`, false);
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

$("#thumb-preview-btn").addEventListener("click", () => {
  refreshThumbnailPreview();
});

["#thumb-line1", "#thumb-line2", "#thumb-line3", "#thumb-line4", "#thumb-difficulty"].forEach(
  (sel) => {
    const el = $(sel);
    if (el) el.addEventListener("input", scheduleThumbnailPreview);
    if (el && el.tagName === "SELECT") el.addEventListener("change", scheduleThumbnailPreview);
  }
);

$("#thumb-image").addEventListener("change", (e) => {
  const file = e.target.files[0];
  const hint = $("#thumb-upload-hint");
  if (file) {
    hint.textContent = `נבחר: ${file.name}`;
    hint.classList.remove("hidden");
    scheduleThumbnailPreview();
  } else {
    hint.classList.add("hidden");
    hint.textContent = "";
  }
});

$("#strum-add-beat")?.addEventListener("click", () => {
  if (state.strumPattern.length >= STRUM_MAX_BEATS) {
    toast(`מקסימום ${STRUM_MAX_BEATS} פעימות`, true);
    return;
  }
  state.strumPattern.push("D");
  buildStrumBeats(state.strumPattern);
  scheduleStrumPreview();
});

$("#strum-remove-beat")?.addEventListener("click", () => {
  if (state.strumPattern.length <= STRUM_MIN_BEATS) {
    toast(`מינימום ${STRUM_MIN_BEATS} פעימות`, true);
    return;
  }
  state.strumPattern.pop();
  buildStrumBeats(state.strumPattern);
  scheduleStrumPreview();
});

$("#strum-preview-btn")?.addEventListener("click", () => {
  refreshStrumPreview();
});

["#strum-title", "#strum-subtitle", "#strum-song"].forEach((sel) => {
  const el = $(sel);
  if (el) el.addEventListener("input", scheduleStrumPreview);
});

$("#strum-background")?.addEventListener("change", (e) => {
  const file = e.target.files[0];
  const hint = $("#strum-upload-hint");
  if (!hint) return;
  if (file) {
    hint.textContent = `נבחר: ${file.name}`;
    hint.classList.remove("hidden");
    scheduleStrumPreview();
  } else {
    hint.classList.add("hidden");
    hint.textContent = "";
  }
});

$("#form-strum")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;

  try {
    const res = await fetch("/api/strum", {
      method: "POST",
      body: getStrumFormData(),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error);

    showStrumPreview(data);
    showResult(
      "result-strum",
      `<p>נוצר: <strong>${escapeHtml(data.filename)}</strong></p>
       <p>נשמר בשולחן העבודה: <strong>${escapeHtml(data.desktop_filename)}</strong></p>
       <a href="${data.file}" download>הורד תמונה</a>
       <br><img src="${data.file}?t=${Date.now()}" alt="פריטה">`
    );
    toast("סלייד פריטה מוכן");
  } catch (err) {
    showResult("result-strum", `<p>${escapeHtml(err.message)}</p>`, false);
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

$("#outro-background")?.addEventListener("change", (e) => {
  const file = e.target.files[0];
  const hint = $("#outro-upload-hint");
  if (!hint) return;
  if (file) {
    hint.textContent = `נבחר: ${file.name}`;
    hint.classList.remove("hidden");
  } else {
    hint.classList.add("hidden");
    hint.textContent = "";
  }
});

$("#form-outro").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const res = await fetch("/api/outro", {
    method: "POST",
    body: fd,
  });
  const data = await res.json();
  if (data.ok) {
    const bgNote = data.used_custom_background ? "רקע: התמונה שהעלית" : "רקע: גיטרה";
    showResult(
      "result-outro",
      `<p>נוצר: ${escapeHtml(data.filename)} · ${escapeHtml(bgNote)}</p>
       <a href="${data.file}" download>הורד</a>
       <br><img src="${data.file}?t=${Date.now()}" alt="סיום">`
    );
    toast("סיום מוכן");
  } else {
    showResult("result-outro", `<p>${escapeHtml(data.error)}</p>`, false);
  }
});

$("#form-artist").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const res = await fetch("/api/artists", { method: "POST", body: fd });
  const data = await res.json();
  if (data.ok) {
    toast(`נשמר: ${data.name}`);
    e.target.reset();
    await loadArtists();
  } else {
    toast(data.error || "שגיאה", true);
  }
});

setVideoMetaLocked($("#video-use-song").checked);
buildStrumBeats();
loadChords();
loadArtists();
loadSession();
