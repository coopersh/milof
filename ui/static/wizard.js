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
    return `<div class="sim-box">Simulator: <button class="btn small" data-scene="${scene}">${label}</button></div>`;
  }

  // --- verdicts used by both flows ---

  function rgbVerdict(s) {
    if (!s || !s.rgb_mean) return "";
    const [r, g, b] = s.rgb_mean;
    const all = [r, g, b];
    const cls = all.every((v) => v >= 180 && v <= 200) && s.clip_hi < 0.1 ? "good"
      : all.every((v) => v >= 160 && v <= 215) ? "warn" : "bad";
    const msg = cls === "good" ? "In range" : Math.max(...all) > 200 ? "Too bright — lower exposure" : "Too dark — raise exposure";
    return `<div class="gauge">R/G/B <b>${fmt(r, 0)} / ${fmt(g, 0)} / ${fmt(b, 0)}</b> ${verdict(cls, msg)}</div>
            <div class="hint">Target 180–200 in every channel, no saturation (now ${fmt(s.clip_hi, 2)}%).</div>`;
  }

  function flatVerdict(s) {
    if (!s || !s.flat) return "";
    const f = s.flat;
    const cls = f.spread <= 6 ? "good" : f.spread <= 12 ? "warn" : "bad";
    const msg = cls === "good" ? "Flat" : cls === "warn" ? "Almost — check the lighting is even" : "Not flat — PRNU needed";
    return `<div class="gauge">Spread along the sensor <b>${fmt(f.spread)}</b> grey levels
            (${fmt(f.spread_pct)}%) ${verdict(cls, msg)}</div>
            <div class="hint">Target: at most 6 grey levels (about ±3). The "Profile along the sensor" chart should be flat.</div>`;
  }

  function sharpVerdictVsReference(s) {
    if (!s || s.sharpness === undefined) return "";
    const ref = calib && calib.focus_reference;
    if (!ref) {
      return `<div class="gauge">Sharpness <b>${fmt(s.sharpness, 2)}</b>
              ${verdict("warn", "No reference value — run a full calibration")}</div>`;
    }
    const ratio = s.sharpness / ref;
    const cls = ratio >= 0.9 ? "good" : ratio >= 0.75 ? "warn" : "bad";
    const msg = cls === "good" ? "In focus" : "Sharpness dropped — the camera may have moved";
    return `<div class="gauge">Sharpness <b>${fmt(s.sharpness, 2)}</b> vs reference
            ${fmt(ref, 2)} (${fmt(ratio * 100, 0)}%) ${verdict(cls, msg)}</div>`;
  }

  // --- steps ---

  const FULL = [
    {
      title: "Network",
      body: () => `<ul>
          <li>Camera on a static IP in <b>192.168.2.x</b> (pylon IP Configurator). Not .1 and not .255.</li>
          <li>The PC's network adapter on the same subnet.</li>
          <li>IP in the settings: <b>${last.settings ? esc(last.settings.camera_info.ip) : "—"}</b>.</li>
        </ul>`,
      live: () => {
        const st = last.status;
        if (!st) return "";
        const ok = st.connected && st.grabbing;
        return `<div class="gauge">${verdict(ok ? "good" : "bad", ok ? "Connected and streaming" : st.connected ? "Connected, not streaming" : "Not connected")}
                <span>Measured rate <b>${fmt(st.line_rate_measured, 0)}</b> Hz</span></div>`;
      },
    },
    {
      title: "Aperture",
      body: () => `<ul>
          <li>The lower ring on the lens. Work at <b>f/5.6–f/8</b>; the current rig uses f/5.6.</li>
          <li>Too open (f/4): shallow depth of field, parts of the bird go out of focus. Too closed: needs an exposure the line rate doesn't allow.</li>
          <li><b>Don't touch the aperture after PRNU</b> — changing it invalidates the calibration.</li>
        </ul>`,
    },
    {
      title: "Defocus",
      body: () => `<ol>
          <li>Loosen the focus screw.</li>
          <li>Turn the upper ring all the way towards infinity.</li>
          <li>Check with the stripe chart: if the stripes disappear in the live view, the lens is defocused — which is what the white calibration needs.</li>
        </ol>${simBox("stripes", "Show stripe chart")}`,
      live: () => {
        const s = last.stats;
        if (!s || s.sharpness === undefined) return "";
        return `<div class="gauge">Sharpness now <b>${fmt(s.sharpness, 2)}</b></div>
                <div class="hint">The number should drop noticeably as the stripes blur.</div>`;
      },
    },
    {
      title: "White sheet & exposure",
      body: () => `<ol>
          <li>Conveyor <b>stopped</b>. A clean white sheet (no creases or marks) under the camera.</li>
          <li>Lower the lift to <b>0</b> in the technician app so the light on the sheet is even.</li>
          <li>Adjust exposure until every channel reads <b>180–200</b> (about 270 µs on the reference rig).</li>
        </ol>
        <div class="slider-row">
          <label>Exposure (µs)</label>
          <input type="number" id="wiz-exposure" step="1" style="width:110px">
          <button class="btn small" id="wiz-exposure-apply">Apply</button>
        </div>${simBox("white", "Show white sheet")}`,
      mount: (card) => {
        const input = card.querySelector("#wiz-exposure");
        if (last.settings) input.value = Math.round(last.settings.camera.exposure_us.value || 0);
        card.querySelector("#wiz-exposure-apply").onclick = () => ctx.setExposure(Number(input.value));
      },
      live: () => rgbVerdict(last.stats),
    },
    {
      title: "Run PRNU",
      body: () => {
        const n = calib && calib.nodes;
        const missing = calib ? calib.missing_prnu : [];
        if (!n || missing.length) {
          return `<p>${verdict("bad", "No shading-correction nodes found on the camera")}</p>
                  <p class="hint">Missing: ${esc(missing.join(", "))}. You can run it manually in pylon Viewer:
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
        return `<p>The camera learns the flatness pattern from the white sheet and stores it in its own memory. Before clicking, make sure:
                  defocused, conveyor stopped, white sheet, channels at 180–200.</p>
                <div class="note nodes">${rows.join("<br>")}</div>
                <div class="actions"><button class="btn danger" id="wiz-prnu">Run PRNU</button></div>`;
      },
      mount: (card) => {
        const btn = card.querySelector("#wiz-prnu");
        if (!btn) return;
        btn.onclick = async () => {
          const ok = await ctx.confirm("Run PRNU?",
            "This writes a new calibration to the camera's memory, replacing the previous one. Make sure the camera is defocused and facing an evenly lit white sheet.");
          if (!ok) return;
          btn.disabled = true;
          btn.textContent = "Running… (a few seconds)";
          try {
            await ctx.api("/api/calibration/prnu", {});
            ctx.toast("PRNU finished and stored in the camera");
            done.add(index);
            await ctx.refreshCalibration();
            go(index + 1);
          } catch (e) {
            ctx.toast(`PRNU failed: ${e.message}`, true);
            btn.disabled = false;
            btn.textContent = "Run PRNU";
          }
        };
      },
      live: () => rgbVerdict(last.stats),
    },
    {
      title: "Verify flatness",
      body: () => `<ol>
          <li>The white sheet should look flat along the whole sensor.</li>
          <li><b>Power-cycle check:</b> switch the camera off and on, click "Reconnect" and check again. If the flatness is gone, save to the camera (UserSet1) so it loads at power-up.</li>
        </ol>
        <div class="actions">
          <button class="btn" id="wiz-record-flat">Record check</button>
          <button class="btn danger" id="wiz-user-set">Save to camera (UserSet1)</button>
        </div>${simBox("white", "Show white sheet")}`,
      mount: (card) => {
        card.querySelector("#wiz-record-flat").onclick = () => record("flat", "verified after PRNU");
        card.querySelector("#wiz-user-set").onclick = async () => {
          const ok = await ctx.confirm("Save settings to the camera?",
            "All current camera settings are saved to UserSet1, and the camera loads them at every power-up.");
          if (!ok) return;
          try {
            await ctx.api("/api/calibration/user-set", {});
            ctx.toast("Saved to UserSet1 and set as the power-up default");
            await ctx.refreshCalibration();
          } catch (e) {
            ctx.toast(`Save failed: ${e.message}`, true);
          }
        };
      },
      live: () => flatVerdict(last.stats),
    },
    {
      title: "Focus",
      body: () => `<ol>
          <li>Stripe chart under the camera, <b>stripes across</b> the sensor line.</li>
          <li>Raise the lift to the working height, <b>33</b>.</li>
          <li>If the image is dark, raise exposure. At 3000 Hz exposure is capped at 333 µs; if you lower the line rate temporarily, <b>restore it afterwards</b>.</li>
          <li>Turn the upper ring until sharpness peaks. Go past the peak and come back to it.</li>
          <li>Lock the screw and click "Record focus value" — this is the reference for shift checks.</li>
        </ol>
        <div class="actions">
          <button class="btn small" id="wiz-peak-reset">Reset peak</button>
          <button class="btn primary" id="wiz-record-focus">Record focus value</button>
        </div>${simBox("stripes", "Show stripe chart")}`,
      mount: (card) => {
        peakSharp = 0;
        card.querySelector("#wiz-peak-reset").onclick = () => { peakSharp = 0; };
        card.querySelector("#wiz-record-focus").onclick = () => record("focus", "focus locked");
      },
      live: () => {
        const s = last.stats;
        if (!s || s.sharpness === undefined) return "";
        peakSharp = Math.max(peakSharp, s.sharpness);
        const ratio = peakSharp ? s.sharpness / peakSharp : 0;
        const cls = ratio >= 0.97 ? "good" : ratio >= 0.85 ? "warn" : "bad";
        return `<div class="gauge">Sharpness <b>${fmt(s.sharpness, 2)}</b>
                peak ${fmt(peakSharp, 2)} ${verdict(cls, cls === "good" ? "At peak" : "Away from peak")}</div>`;
      },
    },
    {
      title: "Calibration board",
      body: () => `<ol>
          <li>The official board (not the stripe chart).</li>
          <li>Put it on the moving conveyor, or move it slowly under the camera — a line scanner needs motion to build a 2-D image.</li>
          <li>Click "Save board image": 2048 full-resolution lines are saved (PNG). The mm-per-pixel calculation is manual for now.</li>
        </ol>
        <div class="actions"><button class="btn primary" id="wiz-board">Save board image</button></div>
        <div id="wiz-board-result" class="hint"></div>`,
      mount: (card) => {
        card.querySelector("#wiz-board").onclick = async () => {
          try {
            const r = await ctx.api("/api/calibration/board", {});
            card.querySelector("#wiz-board-result").innerHTML = `Saved: <bdi>${esc(r.file)}</bdi>`;
            await ctx.refreshCalibration();
          } catch (e) {
            ctx.toast(`Save failed: ${e.message}`, true);
          }
        };
      },
    },
    {
      title: "Finish",
      body: () => `<ul>
          <li>Back to the working line rate (3000 Hz) with exposure at the ceiling (about 330 µs).</li>
          <li>With birds on the line: mean 100–150, saturation below 1% (see the histogram).</li>
          <li>Save the settings to file: "Camera" tab → "Save to file".</li>
          <li>From now on a "Shift check" at the start of each shift is enough.</li>
        </ul>`,
    },
  ];

  const SHIFT = [
    {
      title: "White sheet",
      body: () => `<p>White sheet under the camera, conveyor stopped. The profile should be flat, with no vertical stripes. If not — run a full calibration again (PRNU step).</p>${simBox("white", "Show white sheet")}`,
      live: () => flatVerdict(last.stats),
    },
    {
      title: "Focus",
      body: () => `<p>Stripe chart under the camera at working height. Sharpness should be close to the value recorded at calibration.</p>${simBox("stripes", "Show stripe chart")}`,
      live: () => sharpVerdictVsReference(last.stats),
    },
    {
      title: "Horizontal stripes",
      body: () => `<p>Empty conveyor running, lights on. The live view should show no stripes across time (flicker). If there are, it's a lighting problem, not software.</p>${simBox("birds", "Show running line")}`,
    },
    {
      title: "Record",
      body: () => `<p>Record the check result in the log, with the current values.</p>
        <label class="field">Note <input type="text" id="wiz-shift-note" placeholder="e.g. all OK"></label>
        <div class="actions"><button class="btn primary" id="wiz-shift-record">Record shift check</button></div>`,
      mount: (card) => {
        card.querySelector("#wiz-shift-record").onclick = () =>
          record("shift_check", card.querySelector("#wiz-shift-note").value);
      },
    },
  ];

  async function record(kind, note) {
    try {
      await ctx.api("/api/calibration/record", { kind, note });
      ctx.toast("Recorded in the log");
      done.add(index);
      await ctx.refreshCalibration();
    } catch (e) {
      ctx.toast(`Recording failed: ${e.message}`, true);
    }
  }

  function steps() { return mode === "shift" ? SHIFT : FULL; }

  function render() {
    if (!mode) {
      el.innerHTML = `<p>What do you want to do?</p>
        <div class="wiz-mode">
          <button class="btn primary" data-mode="shift">Start-of-shift check (1 min)</button>
          <button class="btn" data-mode="full">Full calibration</button>
        </div>
        <p class="hint">Full calibration is done at installation, and again when the lights move, the aperture or distance changes, or vertical stripes appear.</p>`;
      el.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { mode = b.dataset.mode; go(0); }));
      return;
    }
    const list = steps();
    const step = list[index];
    el.innerHTML = `
      <div class="wiz-mode">
        <button class="btn small" id="wiz-back-mode">← ${mode === "full" ? "Full calibration" : "Shift check"}</button>
      </div>
      <div class="wiz-steps">${list.map((s, i) =>
        `<button data-step="${i}" class="${i === index ? "current" : done.has(i) ? "done" : ""}">${i + 1}. ${s.title}</button>`).join("")}</div>
      <div class="wiz-card">
        <h4>${index + 1}. ${step.title}</h4>
        ${step.body()}
        <div class="wiz-live"></div>
        <div class="actions">
          <button class="btn" id="wiz-prev" ${index === 0 ? "disabled" : ""}>Back</button>
          <button class="btn primary" id="wiz-next" ${index === list.length - 1 ? "disabled" : ""}>Next</button>
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
