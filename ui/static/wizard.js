// Calibration wizard: full calibration (docs/calibration.md) and the start-of-shift check.
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const Wizard = (() => {
  let el, ctx;
  let mode = null; // "full" | "shift"
  let index = 0;
  let calib = null; // /api/calibration
  let peakSharp = 0;
  let last = { stats: null, status: null, settings: null };
  const done = new Set();

  const fmt = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
  const verdict = (cls, text) => `<span class="verdict ${cls}">${text}</span>`;

  function simBox(scene, label) {
    if (!last.status || last.status.source !== "sim") return "";
    return `<div class="sim-box">סימולטור: <button class="btn small" data-scene="${scene}">${label}</button></div>`;
  }

  // --- verdicts used by both flows ---

  function rgbVerdict(s) {
    if (!s || !s.rgb_mean) return "";
    const [r, g, b] = s.rgb_mean;
    const all = [r, g, b];
    const cls = all.every((v) => v >= 180 && v <= 200) && s.clip_hi < 0.1 ? "good"
      : all.every((v) => v >= 160 && v <= 215) ? "warn" : "bad";
    const msg = cls === "good" ? "בטווח" : Math.max(...all) > 200 ? "בהיר מדי — הורידי חשיפה" : "כהה מדי — העלי חשיפה";
    return `<div class="gauge">R/G/B <b dir="ltr">${fmt(r, 0)} / ${fmt(g, 0)} / ${fmt(b, 0)}</b> ${verdict(cls, msg)}</div>
            <div class="hint">יעד 180–200 בכל ערוץ, בלי רוויה (כרגע <bdi>${fmt(s.clip_hi, 2)}%</bdi>).</div>`;
  }

  function flatVerdict(s) {
    if (!s || !s.flat) return "";
    const f = s.flat;
    const cls = f.spread <= 6 ? "good" : f.spread <= 12 ? "warn" : "bad";
    const msg = cls === "good" ? "אחיד" : cls === "warn" ? "כמעט — בדקי תאורה אחידה" : "לא אחיד — צריך PRNU";
    return `<div class="gauge">הפרש לאורך הסנסור <b dir="ltr">${fmt(f.spread)}</b> רמות אפור
            (<bdi>${fmt(f.spread_pct)}%</bdi>) ${verdict(cls, msg)}</div>
            <div class="hint">יעד: עד 6 רמות אפור (בערך ±3). הגרף "פרופיל לאורך הסנסור" צריך להיות שטוח.</div>`;
  }

  function sharpVerdictVsReference(s) {
    if (!s || s.sharpness === undefined) return "";
    const ref = calib && calib.focus_reference;
    if (!ref) {
      return `<div class="gauge">חדות <b dir="ltr">${fmt(s.sharpness, 2)}</b>
              ${verdict("warn", "אין ערך ייחוס — הריצי כיול מלא")}</div>`;
    }
    const ratio = s.sharpness / ref;
    const cls = ratio >= 0.9 ? "good" : ratio >= 0.75 ? "warn" : "bad";
    const msg = cls === "good" ? "בפוקוס" : "ירידה בחדות — ייתכן שהמצלמה זזה";
    return `<div class="gauge">חדות <b dir="ltr">${fmt(s.sharpness, 2)}</b> מתוך ייחוס
            <bdi>${fmt(ref, 2)}</bdi> (<bdi>${fmt(ratio * 100, 0)}%</bdi>) ${verdict(cls, msg)}</div>`;
  }

  // --- steps ---

  const FULL = [
    {
      title: "רשת וחיבור",
      body: () => `<ul>
          <li>המצלמה על IP קבוע ברשת <bdi>192.168.2.x</bdi> (pylon IP Configurator). לא <bdi>.1</bdi> ולא <bdi>.255</bdi>.</li>
          <li>כרטיס הרשת של המחשב באותה רשת.</li>
          <li>ה-IP בהגדרות: <bdi>${last.settings ? esc(last.settings.camera_info.ip) : "—"}</bdi>.</li>
        </ul>`,
      live: () => {
        const st = last.status;
        if (!st) return "";
        const ok = st.connected && st.grabbing;
        return `<div class="gauge">${verdict(ok ? "good" : "bad", ok ? "מחובר ומשדר" : st.connected ? "מחובר, לא משדר" : "לא מחובר")}
                <span>קצב נמדד <b dir="ltr">${fmt(st.line_rate_measured, 0)}</b> Hz</span></div>`;
      },
    },
    {
      title: "צמצם",
      body: () => `<ul>
          <li>הטבעת התחתונה בעדשה. עובדים ב-<b>f/5.6–f/8</b>; בריג הנוכחי f/5.6.</li>
          <li>פתוח מדי (f/4): עומק שדה רדוד, חלקים מהעוף יוצאים מפוקוס. סגור מדי: צריך חשיפה ארוכה שהקצב לא מאפשר.</li>
          <li><b>אחרי ה-PRNU לא נוגעים בצמצם</b> — שינוי מבטל את הכיול.</li>
        </ul>`,
    },
    {
      title: "הוצאה מפוקוס",
      body: () => `<ol>
          <li>משחררים את בורג הפוקוס.</li>
          <li>מסובבים את הטבעת העליונה עד הסוף לכיוון אינסוף.</li>
          <li>בודקים עם תרשים הפסים: אם הפסים נעלמו בתצוגה — יצאנו מפוקוס, וזה המצב הרצוי לכיול הלבן.</li>
        </ol>${simBox("stripes", "הצג תרשים פסים")}`,
      live: () => {
        const s = last.stats;
        if (!s || s.sharpness === undefined) return "";
        return `<div class="gauge">חדות עכשיו <b dir="ltr">${fmt(s.sharpness, 2)}</b></div>
                <div class="hint">המספר צריך לרדת משמעותית ככל שהפסים נמרחים.</div>`;
      },
    },
    {
      title: "דף לבן וחשיפה",
      body: () => `<ol>
          <li>המסוע <b>עצור</b>. דף לבן חלק (בלי קמטים וסימונים) מתחת למצלמה.</li>
          <li>מורידים את המעלית ל-<b>0</b> באפליקציית הטכנאי, כדי שהתאורה על הדף תהיה אחידה.</li>
          <li>מכוונים חשיפה עד שכל ערוץ ב-<b>180–200</b> (בריג הייחוס בערך 270µs).</li>
        </ol>
        <div class="slider-row">
          <label>חשיפה (µs)</label>
          <input type="number" id="wiz-exposure" step="1" style="width:110px">
          <button class="btn small" id="wiz-exposure-apply">החל</button>
        </div>${simBox("white", "הצג דף לבן")}`,
      mount: (card) => {
        const input = card.querySelector("#wiz-exposure");
        if (last.settings) input.value = Math.round(last.settings.camera.exposure_us.value || 0);
        card.querySelector("#wiz-exposure-apply").onclick = () => ctx.setExposure(Number(input.value));
      },
      live: () => rgbVerdict(last.stats),
    },
    {
      title: "הרצת PRNU",
      body: () => {
        const n = calib && calib.nodes;
        const missing = calib ? calib.missing_prnu : [];
        if (!n || missing.length) {
          return `<p>${verdict("bad", "לא נמצאו הגדרות shading correction במצלמה")}</p>
                  <p class="hint">חסר: <bdi>${esc(missing.join(", "))}</bdi>. אפשר להריץ ידנית ב-pylon Viewer:
                  Image Processing Control → Color Space = Off, Light Source Preset = Off →
                  Shading Correction Selector = PRNU → User → Execute.</p>`;
        }
        const rows = [
          n.color_space && `${n.color_space} = Off`,
          n.light_preset && `${n.light_preset} = Off`,
          `${n.shading_selector} = PRNU`,
          `${n.shading_mode} = User`,
          `${n.shading_create}()  ← execute`,
        ].filter(Boolean).map(esc);
        return `<p>המצלמה תלמד את דפוס האחידות מהדף הלבן ותשמור אותו בזיכרון שלה. ודאי לפני הלחיצה:
                  מחוץ לפוקוס, מסוע עצור, דף לבן, ערוצים ב-180–200.</p>
                <div class="note nodes">${rows.join("<br>")}</div>
                <div class="actions"><button class="btn danger" id="wiz-prnu">הרץ PRNU</button></div>`;
      },
      mount: (card) => {
        const btn = card.querySelector("#wiz-prnu");
        if (!btn) return;
        btn.onclick = async () => {
          const ok = await ctx.confirm("להריץ PRNU?",
            "הפעולה כותבת כיול חדש לזיכרון המצלמה ומחליפה את הקודם. ודאי שהמצלמה מחוץ לפוקוס ומול דף לבן מואר באחידות.");
          if (!ok) return;
          btn.disabled = true;
          btn.textContent = "מריץ… (כמה שניות)";
          try {
            await ctx.api("/api/calibration/prnu", {});
            ctx.toast("PRNU הסתיים ונשמר במצלמה");
            done.add(index);
            await ctx.refreshCalibration();
            go(index + 1);
          } catch (e) {
            ctx.toast(`PRNU נכשל: ${e.message}`, true);
            btn.disabled = false;
            btn.textContent = "הרץ PRNU";
          }
        };
      },
      live: () => rgbVerdict(last.stats),
    },
    {
      title: "אימות אחידות",
      body: () => `<ol>
          <li>התמונה של הדף הלבן צריכה להיות שטוחה לכל אורך הסנסור.</li>
          <li><b>בדיקת כיבוי:</b> כבי והדליקי את המצלמה, לחצי "חיבור מחדש" ובדקי שוב. אם האחידות נעלמה — שמרי למצלמה (UserSet1) כדי שתיטען בהדלקה.</li>
        </ol>
        <div class="actions">
          <button class="btn" id="wiz-record-flat">רשום אימות</button>
          <button class="btn danger" id="wiz-user-set">שמור במצלמה (UserSet1)</button>
        </div>${simBox("white", "הצג דף לבן")}`,
      mount: (card) => {
        card.querySelector("#wiz-record-flat").onclick = () => record("flat", "אימות אחרי PRNU");
        card.querySelector("#wiz-user-set").onclick = async () => {
          const ok = await ctx.confirm("לשמור הגדרות במצלמה?",
            "כל ההגדרות הנוכחיות של המצלמה יישמרו ב-UserSet1, והמצלמה תטען אותן בכל הדלקה.");
          if (!ok) return;
          try {
            await ctx.api("/api/calibration/user-set", {});
            ctx.toast("נשמר ב-UserSet1 ומוגדר כברירת מחדל בהדלקה");
            await ctx.refreshCalibration();
          } catch (e) {
            ctx.toast(`השמירה נכשלה: ${e.message}`, true);
          }
        };
      },
      live: () => flatVerdict(last.stats),
    },
    {
      title: "פוקוס",
      body: () => `<ol>
          <li>תרשים הפסים מתחת למצלמה, <b>הפסים לרוחב</b> קו הסנסור.</li>
          <li>מעלים את המעלית לגובה העבודה <b>33</b>.</li>
          <li>אם התמונה כהה — מעלים חשיפה. בקצב 3000Hz החשיפה מוגבלת ל-333µs; אם מורידים קצב זמנית, <b>להחזיר אחרי</b>.</li>
          <li>מסובבים את הטבעת העליונה עד שהחדות מקסימלית. עוברים את השיא וחוזרים אליו.</li>
          <li>נועלים את הבורג ולוחצים "רשום ערך פוקוס" — זה הייחוס לבדיקות המשמרת.</li>
        </ol>
        <div class="actions">
          <button class="btn small" id="wiz-peak-reset">אפס שיא</button>
          <button class="btn primary" id="wiz-record-focus">רשום ערך פוקוס</button>
        </div>${simBox("stripes", "הצג תרשים פסים")}`,
      mount: (card) => {
        peakSharp = 0;
        card.querySelector("#wiz-peak-reset").onclick = () => { peakSharp = 0; };
        card.querySelector("#wiz-record-focus").onclick = () => record("focus", "פוקוס ננעל");
      },
      live: () => {
        const s = last.stats;
        if (!s || s.sharpness === undefined) return "";
        peakSharp = Math.max(peakSharp, s.sharpness);
        const ratio = peakSharp ? s.sharpness / peakSharp : 0;
        const cls = ratio >= 0.97 ? "good" : ratio >= 0.85 ? "warn" : "bad";
        return `<div class="gauge">חדות <b dir="ltr">${fmt(s.sharpness, 2)}</b>
                שיא <bdi>${fmt(peakSharp, 2)}</bdi> ${verdict(cls, cls === "good" ? "בשיא" : "רחוק מהשיא")}</div>`;
      },
    },
    {
      title: "לוח כיול",
      body: () => `<ol>
          <li>הלוח הרשמי (לא תרשים הפסים).</li>
          <li>מניחים אותו על המסוע הנע, או מזיזים אותו לאט מתחת למצלמה — הסורק צריך תנועה כדי לבנות תמונה דו-ממדית.</li>
          <li>לוחצים "שמור תמונת לוח": נשמרות 2048 שורות ברזולוציה מלאה (PNG). החישוב של מ"מ לפיקסל ידני בשלב הזה.</li>
        </ol>
        <div class="actions"><button class="btn primary" id="wiz-board">שמור תמונת לוח</button></div>
        <div id="wiz-board-result" class="hint"></div>`,
      mount: (card) => {
        card.querySelector("#wiz-board").onclick = async () => {
          try {
            const r = await ctx.api("/api/calibration/board", {});
            card.querySelector("#wiz-board-result").innerHTML = `נשמר: <bdi>${esc(r.file)}</bdi>`;
            await ctx.refreshCalibration();
          } catch (e) {
            ctx.toast(`השמירה נכשלה: ${e.message}`, true);
          }
        };
      },
    },
    {
      title: "סיום",
      body: () => `<ul>
          <li>חזרה לקצב העבודה (3000Hz) וחשיפה בתקרה (כ-330µs).</li>
          <li>עם עופות על הקו: ממוצע 100–150, רוויה מתחת ל-1% (ההיסטוגרמה).</li>
          <li>שומרים את ההגדרות לקובץ: לשונית "מצלמה" ← "שמור לקובץ".</li>
          <li>מעכשיו מספיקה "בדיקת משמרת" בתחילת כל משמרת.</li>
        </ul>`,
    },
  ];

  const SHIFT = [
    {
      title: "דף לבן",
      body: () => `<p>דף לבן מתחת למצלמה, מסוע עצור. הפרופיל צריך להיות שטוח, בלי פסים אנכיים. אם לא — כיול מלא מחדש (שלב PRNU).</p>${simBox("white", "הצג דף לבן")}`,
      live: () => flatVerdict(last.stats),
    },
    {
      title: "פוקוס",
      body: () => `<p>תרשים הפסים מתחת למצלמה, בגובה העבודה. החדות צריכה להיות קרובה לערך שנרשם בכיול.</p>${simBox("stripes", "הצג תרשים פסים")}`,
      live: () => sharpVerdictVsReference(last.stats),
    },
    {
      title: "פסים אופקיים",
      body: () => `<p>מסוע ריק רץ, תאורה דלוקה. בתצוגה החיה לא אמורים להופיע פסים לרוחב הזמן (הבהוב). אם יש — בעיה בתאורה, לא בתוכנה.</p>${simBox("birds", "הצג קו רץ")}`,
    },
    {
      title: "רישום",
      body: () => `<p>רושמים את תוצאת הבדיקה ביומן, עם הערכים הנוכחיים.</p>
        <label class="field">הערה <input type="text" id="wiz-shift-note" placeholder="למשל: הכל תקין"></label>
        <div class="actions"><button class="btn primary" id="wiz-shift-record">רשום בדיקת משמרת</button></div>`,
      mount: (card) => {
        card.querySelector("#wiz-shift-record").onclick = () =>
          record("shift_check", card.querySelector("#wiz-shift-note").value);
      },
    },
  ];

  async function record(kind, note) {
    try {
      await ctx.api("/api/calibration/record", { kind, note });
      ctx.toast("נרשם ביומן");
      done.add(index);
      await ctx.refreshCalibration();
    } catch (e) {
      ctx.toast(`הרישום נכשל: ${e.message}`, true);
    }
  }

  function steps() { return mode === "shift" ? SHIFT : FULL; }

  function render() {
    if (!mode) {
      el.innerHTML = `<p>בחרי מה לעשות:</p>
        <div class="wiz-mode">
          <button class="btn primary" data-mode="shift">בדיקת תחילת משמרת (דקה)</button>
          <button class="btn" data-mode="full">כיול מלא</button>
        </div>
        <p class="hint">כיול מלא עושים בהתקנה, ושוב כשמזיזים תאורה, משנים צמצם או מרחק, או כשמופיעים פסים אנכיים.</p>`;
      el.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { mode = b.dataset.mode; go(0); }));
      return;
    }
    const list = steps();
    const step = list[index];
    el.innerHTML = `
      <div class="wiz-mode">
        <button class="btn small" id="wiz-back-mode">← ${mode === "full" ? "כיול מלא" : "בדיקת משמרת"}</button>
      </div>
      <div class="wiz-steps">${list.map((s, i) =>
        `<button data-step="${i}" class="${i === index ? "current" : done.has(i) ? "done" : ""}">${i + 1}. ${s.title}</button>`).join("")}</div>
      <div class="wiz-card">
        <h4>${index + 1}. ${step.title}</h4>
        ${step.body()}
        <div class="wiz-live"></div>
        <div class="actions">
          <button class="btn" id="wiz-prev" ${index === 0 ? "disabled" : ""}>הקודם</button>
          <button class="btn primary" id="wiz-next" ${index === list.length - 1 ? "disabled" : ""}>הבא</button>
        </div>
      </div>`;
    el.querySelector("#wiz-back-mode").onclick = () => { mode = null; done.clear(); render(); };
    el.querySelectorAll("[data-step]").forEach((b) => (b.onclick = () => go(Number(b.dataset.step))));
    el.querySelector("#wiz-prev").onclick = () => go(index - 1);
    el.querySelector("#wiz-next").onclick = () => { done.add(index); go(index + 1); };
    el.querySelectorAll("[data-scene]").forEach((b) => (b.onclick = () => ctx.setScene(b.dataset.scene)));
    if (step.mount) step.mount(el.querySelector(".wiz-card"));
    updateLive();
  }

  function go(i) {
    index = Math.max(0, Math.min(i, steps().length - 1));
    render();
  }

  function updateLive() {
    if (!mode || !el) return;
    const step = steps()[index];
    const box = el.querySelector(".wiz-live");
    if (box && step.live) box.innerHTML = step.live();
  }

  return {
    init(container, context) { el = container; ctx = context; render(); },
    setCalibration(c) {
      const before = JSON.stringify(calib && calib.nodes);
      calib = c;
      if (mode && JSON.stringify(c.nodes) !== before) render();
    },
    update(data) { Object.assign(last, data); updateLive(); },
  };
})();
