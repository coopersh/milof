// Calibration wizard: full calibration (docs/calibration.md) and the start-of-shift check.
// Each step says what to do, what the operator should see, and why; a status line says
// whether the step looks OK, and the last step sums up every step's result. What a step
// produces (PRNU, UserSet, focus value, board image) is checked by the server after it is
// made; like every mark here, the checks only advise — nothing is blocked on them.
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const Wizard = (() => {
  let el, ctx;
  let mode = null; // "full" | "shift"
  let index = 0;
  let calib = null; // /api/calibration
  let peakSharp = 0; // highest sharpness seen in the current step
  let last = { stats: null, status: null, settings: null };
  let results = []; // per step: "good" | "bad" | "wait" | undefined (not visited)
  let session = {}; // what was done in this wizard run: prnu, focus, board, shift
  const manualDone = new Set(); // manual steps the operator ticked

  const fmt = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
  const verdict = (cls, text) => `<span class="verdict ${cls}">${text}</span>`;
  const good = (msg) => ({ state: "good", msg });
  const bad = (msg) => ({ state: "bad", msg });
  const wait = (msg) => ({ state: "wait", msg });
  const warn = (msg) => ({ state: "warn", msg });

  // server-side checks of a step's output: [{ok, msg}, ...]
  function fromChecks(checks, okMsg) {
    const failed = (checks || []).filter((c) => !c.ok);
    return failed.length ? bad(failed.map((c) => c.msg).join("; ")) : good(okMsg);
  }

  function checksHtml(title, checks) {
    if (!checks || !checks.length) return "";
    return `<div class="wiz-label">${esc(title)}</div><ul class="wiz-summary-list">${checks.map((c) =>
      `<li class="${c.ok ? "good" : "bad"}">${c.ok ? "✓" : "✗"} ${esc(c.msg)}</li>`).join("")}</ul>`;
  }

  function simBox(scene, label) {
    if (!last.status || last.status.source !== "sim") return "";
    return `<div class="sim-box">Simulator: <button class="btn small" data-scene="${scene}">${label}</button></div>`;
  }

  // --- measurements → grade, shared by gauges and checks ---

  function streamGrade() {
    const st = last.status;
    if (!st) return null;
    if (!(st.connected && st.grabbing)) {
      return { cls: "bad", msg: st.connected ? "Connected, not streaming" : "Not connected" };
    }
    const want = last.settings && last.settings.camera.line_rate_hz.value;
    const got = st.line_rate_measured;
    if (want && got && Math.abs(got - want) / want > 0.03) {
      return { cls: "bad", msg: `Streaming at ${fmt(got, 0)} Hz instead of ${fmt(want, 0)} Hz` };
    }
    return { cls: "good", msg: "Connected and streaming" };
  }

  function rgbGrade(s) {
    if (!s || !s.rgb_mean) return null;
    const all = s.rgb_mean;
    if (all.every((v) => v >= 180 && v <= 200) && s.clip_hi < 0.1) return { cls: "good", msg: "In range" };
    const cls = all.every((v) => v >= 160 && v <= 215) ? "warn" : "bad";
    return { cls, msg: Math.max(...all) > 200 ? "Too bright — lower exposure" : "Too dark — raise exposure" };
  }

  function flatGrade(s) {
    if (!s || !s.flat) return null;
    const sp = s.flat.spread;
    if (sp <= 6) return { cls: "good", msg: "Flat" };
    return { cls: sp <= 12 ? "warn" : "bad", msg: sp <= 12 ? "Almost — check the lighting is even" : "Not flat" };
  }

  function refGrade(s) {
    if (!s || s.sharpness === undefined) return null;
    const ref = calib && calib.focus_reference;
    if (!ref) return { cls: "bad", msg: "No focus reference — run a full calibration first", ratio: null };
    const ratio = s.sharpness / ref;
    if (ratio >= 0.9) return { cls: "good", msg: "In focus", ratio };
    return { cls: ratio >= 0.75 ? "warn" : "bad", msg: "Sharpness dropped — the camera may have moved", ratio };
  }

  const fromGrade = (g, waitMsg = "Waiting for data…") =>
    !g ? wait(waitMsg) : g.cls === "good" ? good(g.msg) : bad(g.msg);

  // --- live gauges ---

  function streamGauge() {
    const g = streamGrade();
    if (!g) return "";
    return `<div class="gauge">${verdict(g.cls, g.msg)}
            <span>Measured rate <b>${fmt(last.status.line_rate_measured, 0)}</b> Hz</span></div>`;
  }

  function rgbGauge(s) {
    const g = rgbGrade(s);
    if (!g) return "";
    const [r, gg, b] = s.rgb_mean;
    return `<div class="gauge">R/G/B <b>${fmt(r, 0)} / ${fmt(gg, 0)} / ${fmt(b, 0)}</b> ${verdict(g.cls, g.msg)}</div>
            <div class="hint">Saturated now: ${fmt(s.clip_hi, 2)}%</div>`;
  }

  function flatGauge(s) {
    const g = flatGrade(s);
    if (!g) return "";
    return `<div class="gauge">Spread along the sensor <b>${fmt(s.flat.spread)}</b> grey levels
            (${fmt(s.flat.spread_pct)}%) ${verdict(g.cls, g.msg)}</div>`;
  }

  function sharpGauge(s, withPeak) {
    if (!s || s.sharpness === undefined) return "";
    return `<div class="gauge">Sharpness <b>${fmt(s.sharpness, 2)}</b>
            ${withPeak ? `<span>highest in this step ${fmt(peakSharp, 2)}</span>` : ""}</div>`;
  }

  function refGauge(s) {
    const g = refGrade(s);
    if (!g) return "";
    const ref = calib && calib.focus_reference;
    return `<div class="gauge">Sharpness <b>${fmt(s.sharpness, 2)}</b>
            ${ref ? `<span>reference ${fmt(ref, 2)} (${fmt(g.ratio * 100, 0)}%)</span>` : ""} ${verdict(g.cls, g.msg)}</div>`;
  }

  // --- full calibration ---

  const FULL = [
    {
      title: "Network",
      do: [
        "Nothing, if the status below is green.",
        "If it is red: check the network cable and that the camera LED is on (PoE power), then click <b>Reconnect</b> in the top bar.",
        "On site only: the camera must have an IP on the site network (<b>192.168.2.x</b>, not .1 or .255), set once in pylon IP Configurator, and the same IP in the config (now <b>${ip}</b>).",
      ],
      see: "“Connected and streaming”, and a measured rate close to the configured line rate.",
      why: "Everything after this needs a live picture at the working line rate.",
      live: streamGauge,
      check: () => fromGrade(streamGrade()),
    },
    {
      title: "Aperture",
      do: [
        "Find the <b>lower ring</b> on the lens — the one with f-numbers (2.8, 4, 5.6, 8…).",
        "Set it to <b>5.6</b> and tighten its lock screw.",
      ],
      see: "The live view gets darker as the number goes up, and the brightness below drops.",
      why: "f/5.6–f/8 keeps the whole bird in focus while letting in enough light. Never touch this ring after PRNU (step 5) — changing it voids the calibration.",
      live: (s) => !s ? "" : `<div class="gauge">Brightness (mean) <b>${fmt(s.mean, 0)}</b>
        <span>R/G/B ${fmt(s.rgb_mean[0], 0)} / ${fmt(s.rgb_mean[1], 0)} / ${fmt(s.rgb_mean[2], 0)}</span></div>
        <div class="hint">Turn the ring and watch this number: lower f-number → brighter.</div>`,
      check: "manual",
    },
    {
      title: "Defocus",
      do: [
        "Put the <b>stripe chart</b> under the camera.",
        "Loosen the focus lock screw.",
        "Turn the <b>upper ring</b> all the way towards infinity (∞).",
      ],
      see: "The stripes blur away in the live view and the sharpness number drops well below the highest value seen in this step.",
      why: "PRNU must learn the light and sensor pattern only — not the texture of the paper.",
      controls: () => simBox("stripes", "Show stripe chart"),
      live: (s) => sharpGauge(s, true),
      check: () => {
        const s = last.stats;
        if (!s || s.sharpness === undefined || !peakSharp) return wait("Waiting for data…");
        return s.sharpness <= 0.5 * peakSharp
          ? good(`Defocused (sharpness at ${fmt((s.sharpness / peakSharp) * 100, 0)}% of the highest)`)
          : wait("Still sharp — keep turning the upper ring towards ∞");
      },
    },
    {
      title: "White sheet & exposure",
      do: [
        "Stop the conveyor.",
        "Replace the stripe chart with a clean <b>white sheet</b> (no creases or marks), flat on a rigid board.",
        "Hold it <b>where the birds pass</b> — in the light, square to the camera, covering the whole width — and keep it still. Not against the lens: the lights only meet at the birds' distance.",
        "Change the exposure below and click <b>Apply</b> until the status turns green. If it stays too dark at the maximum, lower the line rate in the Camera tab for now (e.g. 1500 Hz) — you restore it in step 9.",
      ],
      see: "R, G and B all between <b>180 and 200</b>, nothing saturated.",
      why: "PRNU needs a bright, even, unsaturated white to measure each pixel's response.",
      controls: () => `<div class="slider-row">
          <label>Exposure (µs)</label>
          <input type="number" id="wiz-exposure" step="1" style="width:110px">
          <button class="btn small" id="wiz-exposure-apply">Apply</button>
          <span class="hint">max ${last.settings ? fmt(last.settings.derived.exposure_max_us, 0) : "—"}</span>
        </div>${simBox("white", "Show white sheet")}`,
      mount: (card) => {
        const input = card.querySelector("#wiz-exposure");
        if (last.settings) input.value = Math.round(last.settings.camera.exposure_us.value || 0);
        card.querySelector("#wiz-exposure-apply").onclick = () => ctx.setExposure(Number(input.value));
      },
      live: rgbGauge,
      check: () => fromGrade(rgbGrade(last.stats)),
    },
    {
      title: "Run PRNU",
      do: [
        "Check: lens defocused, conveyor stopped, white sheet in place, step 4 green.",
        "Click <b>Run PRNU</b> and confirm.",
      ],
      see: "After a few seconds: “PRNU finished”, then the wizard's own check of the result — the camera reports mode User and the white is flat (spread at most 6). If the camera refuses, its reason is shown here.",
      why: "The camera stores a gain for every pixel so the white comes out flat across the whole width.",
      controls: () => {
        const n = calib && calib.nodes;
        const missing = calib ? calib.missing_prnu : [];
        if (!n || missing.length) {
          return `<p>${verdict("bad", "No shading-correction nodes found on the camera")}</p>
                  <p class="hint">Missing: ${esc(missing.join(", "))}. Run it manually in pylon Viewer:
                  Color Space = Off, Light Source Preset = Off → Shading Correction Selector = PRNU → User → Execute.</p>`;
        }
        const rows = [
          n.color_space && `${n.color_space} = Off`,
          n.light_preset && `${n.light_preset} = Off`,
          `${n.shading_selector} = PRNU`,
          `${n.shading_mode} = User`,
          `${n.shading_create}()  ← execute`,
        ].filter(Boolean).map(esc);
        return `<div class="hint">The camera settings this will change:</div>
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
            session.prnu = await ctx.api("/api/calibration/prnu", {});
            session.prnuError = null;
            ctx.toast("PRNU finished and stored in the camera");
            await ctx.refreshCalibration();
            render();
          } catch (e) {
            session.prnuError = e.message;
            ctx.toast(`PRNU failed: ${e.message}`, true);
            btn.disabled = false;
            btn.textContent = "Run PRNU";
            updateLive();
          }
        };
      },
      live: (s) => rgbGauge(s) + (session.prnu ? checksHtml("Result check", session.prnu.checks) : ""),
      fromAction: true,
      check: () => session.prnu ? fromChecks(session.prnu.checks, "PRNU done, camera in User mode and the white is flat")
        : session.prnuError ? bad(`PRNU failed: ${session.prnuError}`) : wait("PRNU not run yet"),
    },
    {
      title: "Verify flatness",
      do: [
        "Keep the white sheet in place and check the status below is green. Click <b>Record check</b>.",
        "Power-cycle test: unplug the camera's network/PoE cable for 10 s, plug it back, click <b>Reconnect</b> in the top bar, and look again.",
        "If it is no longer flat after the restart: go back to step 5, run PRNU again, then click <b>Save to camera (UserSet1)</b> here so it loads at every power-up.",
      ],
      see: "Spread along the sensor at most <b>6</b> grey levels, and a flat line in the “Profile along the sensor” chart. After the restart the spread should be close to the value right after PRNU. If you saved to UserSet1, the wizard checks the camera now powers up with it.",
      why: "Confirms the correction works and survives a power cut.",
      controls: () => `<div class="actions">
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
            session.userSet = await ctx.api("/api/calibration/user-set", {});
            ctx.toast("Saved to UserSet1 and set as the power-up default");
            await ctx.refreshCalibration();
          } catch (e) {
            ctx.toast(`Save failed: ${e.message}`, true);
          }
        };
      },
      live: (s) => {
        const after = session.prnu && session.prnu.flat_after;
        return flatGauge(s)
          + (after ? `<div class="hint">Right after PRNU: ${fmt(after.spread)} grey levels</div>` : "")
          + (session.userSet ? checksHtml("UserSet1 check", session.userSet.checks) : "");
      },
      check: () => {
        const us = session.userSet && fromChecks(session.userSet.checks, "");
        if (us && us.state === "bad") return bad(`UserSet1: ${us.msg}`);
        return fromGrade(flatGrade(last.stats));
      },
    },
    {
      title: "Focus",
      do: [
        "Replace the white sheet with the <b>stripe chart</b> in the same place (where the birds pass), stripes crossing the camera's long axis.",
        "Turn the <b>upper ring</b> slowly until the status says “at peak”. Go a little past it and come back.",
        "Tighten the focus lock screw and click <b>Record focus value</b>.",
      ],
      see: "The sharpness number climbs to a maximum, then “✓ Focus recorded”. If the picture is too dark, raise exposure (up to the maximum).",
      why: "The recorded value is the reference the shift check compares against.",
      controls: () => `<div class="actions">
          <button class="btn small" id="wiz-peak-reset">Reset peak</button>
          <button class="btn primary" id="wiz-record-focus">Record focus value</button>
        </div>${simBox("stripes", "Show stripe chart")}`,
      mount: (card) => {
        card.querySelector("#wiz-peak-reset").onclick = () => { peakSharp = 0; };
        card.querySelector("#wiz-record-focus").onclick = async () => {
          if (await record("focus", "focus locked")) session.focus = last.stats && last.stats.sharpness;
          updateLive();
        };
      },
      live: (s) => sharpGauge(s, true),
      check: () => {
        if (session.focus) {
          const prev = calib && calib.focus_previous;
          const change = prev ? session.focus / prev - 1 : 0;
          if (Math.abs(change) > 0.25) {
            return warn(`Focus recorded (${fmt(session.focus, 2)}), ${fmt(Math.abs(change) * 100, 0)}% ${change > 0 ? "higher" : "lower"} than the last calibration (${fmt(prev, 2)}). Expected only if the lens or the camera distance changed`);
          }
          return good(`Focus recorded (${fmt(session.focus, 2)})${prev ? `, close to the last calibration (${fmt(prev, 2)})` : ""}`);
        }
        const s = last.stats;
        if (!s || s.sharpness === undefined || !peakSharp) return wait("Waiting for data…");
        return s.sharpness >= 0.97 * peakSharp
          ? wait("At peak — lock the screw and click Record focus value")
          : wait(`Not at peak (${fmt((s.sharpness / peakSharp) * 100, 0)}% of the highest) — keep turning`);
      },
    },
    {
      title: "Calibration board",
      do: [
        "Use the official calibration board (not the stripe chart).",
        "Start it moving under the camera — on the running conveyor, or slide it slowly by hand.",
        "Click <b>Save board image</b> just as the board reaches the camera: it records the next ~0.7 s.",
        "Look at the picture that appears: the whole board should be in it. If a check is red, try again.",
      ],
      see: "The saved board image, and green checks: brightness OK, board found and not cut off, board moving.",
      why: "The board image gives the millimetres per pixel (worked out by hand for now).",
      controls: () => `<div class="actions"><button class="btn primary" id="wiz-board">Save board image</button></div>
        ${session.board ? `<div class="hint">Saved: <bdi>${esc(session.board.file)}</bdi></div>
          <img class="wiz-board" alt="Saved board image" src="/api/calibration/board.jpg?t=${encodeURIComponent(session.board.time)}">
          ${checksHtml("Image check", session.board.checks)}` : ""}`,
      mount: (card) => {
        card.querySelector("#wiz-board").onclick = async (ev) => {
          ev.target.disabled = true;
          try {
            session.board = await ctx.api("/api/calibration/board", {});
            await ctx.refreshCalibration();
          } catch (e) {
            ctx.toast(`Save failed: ${e.message}`, true);
          }
          render();
        };
      },
      fromAction: true,
      check: () => session.board ? fromChecks(session.board.checks, "Board image saved and looks usable")
        : wait("No board image saved yet"),
    },
    {
      title: "Finish",
      do: [
        "Camera tab: line rate back to the working value (3000 Hz) if you changed it, exposure at the maximum (about 330 µs).",
        "Camera tab: click <b>Save to file</b>.",
        "With birds running: check the live view — Mean 100–150 and Saturated below 1%.",
      ],
      see: "No warning in the top bar, settings saved to file, and the summary below all green.",
      why: "Leaves the camera in its production settings. From now on a start-of-shift check is enough.",
      controls: () => `<div class="wiz-summary"></div>`,
      live: streamGauge,
      check: () => {
        const g = streamGrade();
        if (!g) return wait("Waiting for data…");
        if (g.cls !== "good") return bad(g.msg);
        if (last.status.warning) return bad(last.status.warning);
        const unsaved = last.status.unsaved || [];
        if (unsaved.length) return bad(`Not saved to file: ${unsaved.join(", ")} — click Save to file in the Camera tab`);
        return good("Camera at its working rate, no warnings, settings saved to file");
      },
      summary: true,
    },
  ];

  // --- start-of-shift check ---

  const SHIFT = [
    {
      title: "White sheet",
      do: ["Stop the conveyor and put the white sheet under the camera."],
      see: "Spread along the sensor at most 6 grey levels, no vertical stripes in the live view.",
      why: "Shows the PRNU correction is still valid. If not: run a full calibration from step 3.",
      controls: () => simBox("white", "Show white sheet"),
      live: flatGauge,
      check: () => fromGrade(flatGrade(last.stats)),
    },
    {
      title: "Focus",
      do: ["Hold the stripe chart where the birds pass, in the same place as at calibration."],
      see: "Sharpness at least 90% of the value recorded at calibration.",
      why: "A drop means the camera moved, or the chart is not where the birds pass. If the camera moved: full calibration, step 7.",
      controls: () => simBox("stripes", "Show stripe chart"),
      live: refGauge,
      check: () => fromGrade(refGrade(last.stats)),
    },
    {
      title: "Flicker",
      do: ["Remove the chart, run the conveyor empty with the lights on, and watch the live view for about 10 seconds."],
      see: "No bright or dark bands across the full height of the live view.",
      why: "Bands mean the lights flicker — a lighting/power problem, not software.",
      controls: () => simBox("birds", "Show running line"),
      check: "manual",
    },
    {
      title: "Record",
      do: ["Add a note if anything was off, then click <b>Record shift check</b>."],
      see: "“Recorded in the log” and a new line in the Calibration log below.",
      why: "Keeps a dated record of each shift's check, with the measured values.",
      controls: () => `<div class="wiz-summary"></div>
        <label class="field">Note <input type="text" id="wiz-shift-note" placeholder="e.g. all OK"></label>
        <div class="actions"><button class="btn primary" id="wiz-shift-record">Record shift check</button></div>`,
      mount: (card) => {
        card.querySelector("#wiz-shift-record").onclick = async () => {
          const note = card.querySelector("#wiz-shift-note").value.trim();
          const sum = summaryText();
          if (await record("shift_check", note ? `${note} · ${sum}` : sum)) session.shift = true;
          updateLive();
        };
      },
      fromAction: true,
      check: () => session.shift ? good("Recorded") : wait("Not recorded yet"),
      summary: true,
    },
  ];

  async function record(kind, note) {
    try {
      await ctx.api("/api/calibration/record", { kind, note });
      ctx.toast("Recorded in the log");
      await ctx.refreshCalibration();
      return true;
    } catch (e) {
      ctx.toast(`Recording failed: ${e.message}`, true);
      return false;
    }
  }

  function steps() { return mode === "shift" ? SHIFT : FULL; }

  // --- results ---

  function stepResult(i) {
    const step = steps()[i];
    if (step.check === "manual") return manualDone.has(i) ? "good" : results[i];
    return results[i];
  }

  function summaryRows() {
    return steps().map((s, i) => ({ i, title: s.title, r: stepResult(i) })).filter((x) => !steps()[x.i].summary);
  }

  const MARK = { good: ["✓", "OK"], warn: ["!", "CHECK"], bad: ["✗", "FAIL"] };

  function summaryText() {
    return summaryRows().map((x) => `${x.title} ${MARK[x.r] ? MARK[x.r][1] : "not checked"}`).join(", ");
  }

  function summaryHtml() {
    const rows = summaryRows();
    const failed = rows.filter((x) => x.r === "bad");
    const open = rows.filter((x) => !MARK[x.r]);
    const warned = rows.filter((x) => x.r === "warn");
    const head = failed.length ? verdict("bad", `${failed.length} step(s) not OK`)
      : open.length ? verdict("warn", `${open.length} step(s) not checked yet`)
        : warned.length ? verdict("warn", `OK, ${warned.length} step(s) worth a second look`)
          : verdict("good", "Everything looks OK");
    return `<div class="wiz-label">Summary</div><div class="gauge">${head}</div>
      <ul class="wiz-summary-list">${rows.map((x) =>
        `<li class="${x.r || ""}">${MARK[x.r] ? MARK[x.r][0] : "–"} ${x.i + 1}. ${x.title}</li>`).join("")}</ul>`;
  }

  // --- rendering ---

  function statusHtml(r) {
    const next = index < steps().length - 1 ? " Click Next." : "";
    if (r.state === "good") return `<div class="wiz-status good">✓ Looks good — ${esc(r.msg)}.${next}</div>`;
    if (r.state === "bad") return `<div class="wiz-status bad">✗ Not OK — ${esc(r.msg)}</div>`;
    if (r.state === "warn") return `<div class="wiz-status wait">! Worth a second look — ${esc(r.msg)}.${next}</div>`;
    return `<div class="wiz-status wait">… ${esc(r.msg)}</div>`;
  }

  function render() {
    if (!mode) {
      el.innerHTML = `<p>What do you want to do?</p>
        <div class="wiz-mode">
          <button class="btn primary" data-mode="shift">Start-of-shift check (1 min)</button>
          <button class="btn" data-mode="full">Full calibration</button>
        </div>
        <p class="hint">Full calibration is done at installation, and again when the lights move, the aperture or distance changes, or vertical stripes appear.</p>`;
      el.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => {
        mode = b.dataset.mode;
        results = [];
        session = {};
        manualDone.clear();
        go(0);
      }));
      return;
    }
    const list = steps();
    const step = list[index];
    const ip = last.settings ? esc(last.settings.camera_info.ip) : "—";
    const doItems = step.do.map((d) => d.replace("${ip}", ip));
    el.innerHTML = `
      <div class="wiz-mode">
        <button class="btn small" id="wiz-back-mode">← ${mode === "full" ? "Full calibration" : "Shift check"}</button>
      </div>
      <div class="wiz-steps">${list.map((s, i) => `<button data-step="${i}">${i + 1}. ${s.title}</button>`).join("")}</div>
      <div class="wiz-card">
        <h4>${index + 1}. ${step.title}</h4>
        <div class="wiz-label">Do</div>
        <ol>${doItems.map((d) => `<li>${d}</li>`).join("")}</ol>
        ${step.controls ? step.controls() : ""}
        <div class="wiz-label">You should see</div>
        <p class="wiz-see">${step.see}</p>
        <div class="wiz-live"></div>
        ${step.check === "manual"
          ? `<label class="check wiz-manual"><input type="checkbox" id="wiz-manual" ${manualDone.has(index) ? "checked" : ""}>
             Done</label>`
          : `<div class="wiz-status-box"></div>`}
        <p class="wiz-why"><b>Why:</b> ${step.why}</p>
        <div class="actions">
          <button class="btn" id="wiz-prev" ${index === 0 ? "disabled" : ""}>Back</button>
          <button class="btn primary" id="wiz-next" ${index === list.length - 1 ? "disabled" : ""}>Next</button>
        </div>
      </div>`;
    el.querySelector("#wiz-back-mode").onclick = () => { mode = null; render(); };
    el.querySelectorAll("[data-step]").forEach((b) => (b.onclick = () => go(Number(b.dataset.step))));
    el.querySelector("#wiz-prev").onclick = () => go(index - 1);
    el.querySelector("#wiz-next").onclick = () => go(index + 1);
    const manual = el.querySelector("#wiz-manual");
    if (manual) {
      manual.onchange = () => {
        if (manual.checked) manualDone.add(index); else manualDone.delete(index);
        updateLive();
      };
    }
    el.querySelectorAll("[data-scene]").forEach((b) => (b.onclick = () => ctx.setScene(b.dataset.scene)));
    if (step.mount) step.mount(el.querySelector(".wiz-card"));
    updateLive();
  }

  function go(i) {
    index = Math.max(0, Math.min(i, steps().length - 1));
    peakSharp = 0;
    render();
  }

  function updateLive() {
    if (!mode || !el) return;
    const step = steps()[index];
    const s = last.stats;
    if (s && s.sharpness !== undefined) peakSharp = Math.max(peakSharp, s.sharpness);
    const live = el.querySelector(".wiz-live");
    if (live) live.innerHTML = step.live ? step.live(s) : "";
    // steps judged by an action done in this run (PRNU, recorded focus, …) stay current even
    // when the operator is elsewhere; measurement steps keep the result from their last visit
    steps().forEach((st, i) => {
      if (st.fromAction) results[i] = st.check().state;
    });
    if (step.check !== "manual") {
      const r = step.check();
      results[index] = r.state;
      const box = el.querySelector(".wiz-status-box");
      if (box) box.innerHTML = statusHtml(r);
    }
    const sum = el.querySelector(".wiz-summary");
    if (sum) sum.innerHTML = summaryHtml();
    el.querySelectorAll("[data-step]").forEach((b) => {
      const i = Number(b.dataset.step);
      const r = stepResult(i);
      const cls = { good: "ok", bad: "fail", warn: "warn" }[r] || "";
      b.className = [i === index ? "current" : "", cls].join(" ").trim();
    });
  }

  return {
    init(container, context) { el = container; ctx = context; render(); },
    setCalibration(c) {
      const before = JSON.stringify(calib && calib.nodes);
      calib = c;
      if (mode && JSON.stringify(c.nodes) !== before) render();
    },
    update(data) {
      const settingsArrived = data.settings && !last.settings;
      Object.assign(last, data);
      if (settingsArrived && mode) render(); else updateLive();
    },
  };
})();
