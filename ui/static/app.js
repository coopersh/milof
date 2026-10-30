// Acquisition station UI: tabs, polling, settings forms, capture control.
const $ = (sel) => document.querySelector(sel);
const fmt = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));

let settings = null;
let status = null;
let activeTab = "capture";

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

let toastTimer;
function toast(msg, bad = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast${bad ? " bad" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), bad ? 7000 : 3500);
}

function confirmDialog(title, text) {
  const dlg = $("#confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-body").textContent = text;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
  });
}

function setDirty(on) { $("#pill-dirty").classList.toggle("hidden", !on); }

// --- tabs ---

document.querySelectorAll(".tab").forEach((btn) => {
  btn.onclick = () => {
    activeTab = btn.dataset.tab;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab-body").forEach((b) =>
      b.classList.toggle("hidden", b.id !== `tab-${activeTab}`));
    if (activeTab === "calibration") refreshCalibration();
    if (["camera", "segmentation", "storage"].includes(activeTab)) loadSettings();
  };
});

// --- live preview: request the next frame when the previous one has loaded ---

const preview = $("#preview");
function nextPreview(delay) {
  setTimeout(() => {
    const w = Math.min(1400, Math.round(($(".preview-wrap").clientWidth || 900) * (window.devicePixelRatio || 1)));
    preview.src = `/api/preview.jpg?width=${w}&t=${Date.now()}`;
  }, delay);
}
preview.onload = () => { $("#preview-empty").classList.add("hidden"); nextPreview(120); };
preview.onerror = () => { $("#preview-empty").classList.remove("hidden"); nextPreview(1000); };

// --- stats ---

function setGrade(el, cls) {
  el.classList.remove("good", "warn", "bad");
  if (cls) el.classList.add(cls);
}

async function pollStats() {
  try {
    const s = await api("/api/stats");
    if (s && s.mean !== undefined) {
      $("#s-mean").textContent = fmt(s.mean);
      setGrade($("#s-mean"), s.mean >= 100 && s.mean <= 150 ? "good" : "warn");
      $("#s-minmax").textContent = `${s.min} / ${s.max}`;
      $("#s-cliphi").textContent = `${fmt(s.clip_hi, 2)}%`;
      setGrade($("#s-cliphi"), s.clip_hi < 1 ? "good" : "bad");
      $("#s-cliplo").textContent = `${fmt(s.clip_lo, 2)}%`;
      setGrade($("#s-cliplo"), s.clip_lo < 5 ? "good" : "warn");
      $("#s-rgb").textContent = s.rgb_mean.map((v) => fmt(v, 0)).join(" / ");
      $("#s-sharp").textContent = fmt(s.sharpness, 2);
      $("#s-flat").textContent = s.flat ? `${fmt(s.flat.spread)} (${fmt(s.flat.spread_pct)}%)` : "—";
      Charts.hist($("#hist"), s.hist);
      Charts.profile($("#profile"), s.profile, activeTab === "calibration" ? [180, 200] : null);
      Wizard.update({ stats: s });
    }
  } catch { /* status poll shows connection problems */ }
  setTimeout(pollStats, 400);
}

// --- status ---

function mmss(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function renderStatus(st) {
  status = st;
  const src = $("#pill-source");
  src.textContent = st.source === "sim" ? "סימולטור" : `מצלמה ${st.device || ""}`;
  src.className = `pill${st.source === "sim" ? " warn" : ""}`;
  const stream = $("#pill-stream");
  if (st.connected && st.grabbing) { stream.textContent = "משדר"; stream.className = "pill ok"; }
  else if (st.connected) { stream.textContent = "לא משדר"; stream.className = "pill warn"; }
  else { stream.textContent = "מנותק"; stream.className = "pill bad"; }
  $("#pill-capture").classList.toggle("hidden", !st.capture);
  $("#pill-capture").className = `pill rec${st.capture ? "" : " hidden"}`;
  $("#m-rate").textContent = st.line_rate_measured ? `${fmt(st.line_rate_measured, 0)} Hz` : "—";
  $("#m-lost").textContent = st.lost_frames;
  $("#error-bar").classList.toggle("hidden", !st.error && !st.warning);
  $("#error-text").textContent = st.error || st.warning || "";

  const cap = st.capture;
  const btn = $("#btn-capture");
  btn.textContent = cap ? "עצור צילום" : "התחל צילום";
  btn.classList.toggle("stop", !!cap);
  btn.classList.toggle("primary", !cap);
  $("#capture-mode").disabled = !!cap;
  $("#c-time").textContent = cap ? mmss(cap.seconds) : "—";
  $("#c-cut").textContent = cap ? cap.cut : "—";
  $("#c-written").textContent = cap ? cap.written : "—";
  $("#c-dropped").textContent = cap ? cap.dropped : "—";
  setGrade($("#c-dropped"), cap ? (cap.dropped ? "bad" : "good") : null);
  $("#c-failed").textContent = cap ? cap.failed : "—";
  setGrade($("#c-failed"), cap ? (cap.failed ? "bad" : "good") : null);
  $("#c-disk").textContent = `${fmt(st.disk_free_gb)} GB`;
  setGrade($("#c-disk"), st.disk_free_gb < 25 ? "bad" : null);
  $("#c-dir").textContent = st.data_dir;

  const lc = st.last_capture;
  $("#last-capture").classList.toggle("hidden", !lc || !!cap);
  if (lc) {
    $("#last-capture").textContent =
      `הרצה אחרונה (${lc.started}): ${lc.written} נשמרו מתוך ${lc.cut} בתוך ${mmss(lc.seconds)}; נזרקו ${lc.dropped}, כשלים ${lc.failed}.`;
  }

  const cut = st.last_cut;
  $("#seg-live").textContent = cut
    ? `חיתוך אחרון: ניגודיות ${fmt(cut.contrast, 2)} · תיקון מרכז ${fmt(cut.error, 1)} שורות · מחזור ${fmt(cut.period, 1)} שורות`
    : "הדליקי 'הצג מרכזי חיתוך' או התחילי צילום כדי לראות את החיתוך.";
  $("#show-cuts").checked = st.show_cuts;
  Wizard.update({ status: st });
}

async function pollStatus() {
  try {
    renderStatus(await api("/api/status"));
  } catch (e) {
    $("#pill-stream").textContent = "אין תקשורת עם השרת";
    $("#pill-stream").className = "pill bad";
  }
  setTimeout(pollStatus, 1000);
}

// --- recent images ---

async function pollRecent() {
  if (activeTab === "capture") {
    try {
      const items = await api("/api/recent");
      const box = $("#recent");
      if (items.length) {
        box.innerHTML = items.slice().reverse().map((r) =>
          `<figure><img src="/api/recent/${r.seq}.jpg?f=${encodeURIComponent(r.file)}" alt="">
           <figcaption>#${r.seq} · ${esc(r.time)} · c ${fmt(r.contrast, 1)}</figcaption></figure>`).join("");
      }
    } catch { /* next poll */ }
  }
  setTimeout(pollRecent, 2000);
}

// --- settings ---

const CAM_FIELDS = ["exposure_us", "line_rate_hz", "lines_per_frame", "packet_size_bytes", "reverse_x"];
const SEG_FIELDS = ["method", "birds_per_min", "period_lines", "window_factor", "track_gain",
  "min_contrast", "profile_sign", "tile_lines"];
const ST_FIELDS = ["jpeg_quality", "rotate", "flip_vertical", "flip_horizontal"];

function fillField(id, value) {
  const el = $(id);
  if (!el || document.activeElement === el) return;
  if (el.type === "checkbox") el.checked = !!value;
  else el.value = value === null || value === undefined ? "" : value;
}

function readField(id) {
  const el = $(id);
  return el.type === "checkbox" ? el.checked : el.value;
}

function renderSettings(s) {
  settings = s;
  const ci = s.camera_info;
  $("#camera-info").innerHTML = [
    ["דגם", ci.model], ["סריאלי", ci.serial ?? "—"], ["IP", ci.ip],
  ].map(([k, v]) => `<div><span>${k}</span><b dir="ltr">${esc(v)}</b></div>`).join("");
  CAM_FIELDS.forEach((f) => {
    const v = s.camera[f].value;
    fillField(`#cam-${f}`, f === "exposure_us" && v !== null ? Math.round(v) : v);
  });
  const expMax = s.derived.exposure_max_us;
  $("#exp-max").textContent = fmt(expMax, 0);
  const range = $("#exp-range");
  range.max = Math.max(Math.round(expMax), Math.round(s.camera.exposure_us.value || 0), 10);
  if (document.activeElement !== range) range.value = Math.round(s.camera.exposure_us.value || 0);

  SEG_FIELDS.forEach((f) => fillField(`#seg-${f}`, s.segmentation[f]));
  const d = s.derived;
  $("#seg-derived").innerHTML = [
    ["מחזור נומינלי", `${fmt(d.nominal_period_lines)} שורות`],
    ["זמן לעוף", `${fmt(d.bird_ms)} ms`],
    ["חלון", `${d.window_lines} שורות`],
  ].map(([k, v]) => `<div><span>${k}</span><b>${esc(v)}</b></div>`).join("");

  ST_FIELDS.forEach((f) => fillField(`#st-${f}`, s.storage[f]));
  $("#st-dir").textContent = s.storage.data_dir;
  Wizard.update({ settings: s });
}

async function loadSettings() {
  try { renderSettings(await api("/api/settings")); }
  catch (e) { toast(`טעינת הגדרות נכשלה: ${e.message}`, true); }
}

async function applySettings(path, values) {
  try {
    renderSettings(await api(path, values));
    setDirty(true);
    toast("הוחל");
  } catch (e) {
    toast(e.message, true);
    loadSettings();
  }
}

function changed(fields, prefix, current) {
  const out = {};
  fields.forEach((f) => {
    const v = readField(`#${prefix}-${f}`);
    const cur = current[f] && typeof current[f] === "object" && "value" in current[f] ? current[f].value : current[f];
    if (String(v) !== String(cur ?? "")) out[f] = v;
  });
  return out;
}

$("#btn-apply-camera").onclick = () => {
  const values = changed(CAM_FIELDS, "cam", settings.camera);
  if (!Object.keys(values).length) return toast("אין שינויים");
  applySettings("/api/settings/camera", values);
};
$("#btn-apply-seg").onclick = () => {
  const values = changed(SEG_FIELDS, "seg", settings.segmentation);
  if (!Object.keys(values).length) return toast("אין שינויים");
  applySettings("/api/settings/segmentation", values);
};
$("#btn-apply-storage").onclick = () => {
  const values = changed(ST_FIELDS, "st", settings.storage);
  if (!Object.keys(values).length) return toast("אין שינויים");
  applySettings("/api/settings/storage", values);
};

let expTimer;
function setExposure(v) {
  clearTimeout(expTimer);
  expTimer = setTimeout(() => applySettings("/api/settings/camera", { exposure_us: v }), 250);
}
$("#exp-range").oninput = (e) => { $("#cam-exposure_us").value = e.target.value; setExposure(Number(e.target.value)); };
$("#cam-exposure_us").onchange = (e) => setExposure(Number(e.target.value));

document.querySelectorAll("[data-save]").forEach((b) => {
  b.onclick = async () => {
    try {
      const r = await api("/api/settings/save", {});
      setDirty(false);
      toast(`נשמר ל-${r.path}`);
    } catch (e) { toast(`השמירה נכשלה: ${e.message}`, true); }
  };
});

$("#show-cuts").onchange = async (e) => {
  try { await api("/api/cuts", { on: e.target.checked }); }
  catch (err) { toast(err.message, true); }
};

// --- capture ---

$("#btn-capture").onclick = async () => {
  const btn = $("#btn-capture");
  btn.disabled = true;
  try {
    if (status && status.capture) {
      const r = await api("/api/capture/stop", {});
      toast(`נעצר: ${r.written} תמונות נשמרו`);
    } else {
      await api("/api/capture/start", { mode: $("#capture-mode").value });
      $("#recent").innerHTML = "";
      toast("הצילום התחיל");
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
  }
};

$("#btn-reconnect").onclick = async () => {
  try { renderStatus(await api("/api/reconnect", {})); toast("חובר מחדש"); }
  catch (e) { toast(e.message, true); }
};

// --- calibration ---

async function refreshCalibration() {
  try {
    const c = await api("/api/calibration");
    Wizard.setCalibration(c);
    const names = { prnu: "PRNU", user_set_saved: "נשמר ל-UserSet", focus: "פוקוס", flat: "אחידות",
      board: "לוח כיול", shift_check: "בדיקת משמרת", exposure: "חשיפה" };
    $("#calib-log").innerHTML = c.log.length ? c.log.slice().reverse().map((e) => {
      const bits = [];
      if (["focus", "shift_check"].includes(e.action) && e.sharpness != null) bits.push(`חדות ${fmt(e.sharpness, 2)}`);
      if (["flat", "shift_check"].includes(e.action) && e.flat) bits.push(`אחידות ${fmt(e.flat.spread)}`);
      if (e.action === "prnu" && e.flat_before) bits.push(`אחידות לפני ${fmt(e.flat_before.spread)}`);
      if (e.note) bits.push(esc(e.note));
      if (e.file) bits.push(`<bdi>${esc(e.file)}</bdi>`);
      return `<div><time>${esc(e.time.replace("T", " "))}</time><b>${esc(names[e.action] || e.action)}</b> ${bits.join(" · ")}</div>`;
    }).join("") : `<p class="hint">היומן ריק.</p>`;
  } catch (e) {
    toast(`טעינת נתוני כיול נכשלה: ${e.message}`, true);
  }
}

Wizard.init($("#wizard"), {
  api, toast, confirm: confirmDialog, refreshCalibration,
  setExposure: (v) => applySettings("/api/settings/camera", { exposure_us: v }),
  setScene: async (scene) => {
    try { await api("/api/sim/scene", { scene }); } catch (e) { toast(e.message, true); }
  },
});

loadSettings();
refreshCalibration();
pollStatus();
pollStats();
pollRecent();
nextPreview(0);
