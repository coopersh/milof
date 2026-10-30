// Small canvas charts: luminance histogram and per-sensor-pixel profile.
const Charts = (() => {
  function setup(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = "11px Segoe UI, Arial";
    return { ctx, w, h };
  }

  function band(ctx, x0, x1, y0, y1, color) {
    ctx.fillStyle = color;
    ctx.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
  }

  // bins: normalised counts (0..1) over grey levels 0..255
  function hist(canvas, bins, target = [100, 150]) {
    const { ctx, w, h } = setup(canvas);
    const plotH = h - 16;
    const x = (v) => (v / 256) * w;
    band(ctx, x(target[0]), x(target[1]), 0, plotH, "rgba(60,207,122,0.15)");
    const bw = w / bins.length;
    ctx.fillStyle = "#cfd8de";
    bins.forEach((v, i) => {
      const bh = v * (plotH - 4);
      ctx.fillRect(i * bw, plotH - bh, Math.max(1, bw - 1), bh);
    });
    ctx.fillStyle = "#8a9aa6";
    [0, 64, 128, 192, 255].forEach((v) => ctx.fillText(String(v), Math.min(x(v), w - 20), h - 3));
  }

  // prof: {r, g, b: arrays over the sensor, sensor_px}; target: optional [lo, hi] grey levels
  function profile(canvas, prof, target) {
    const { ctx, w, h } = setup(canvas);
    const plotH = h - 16;
    const y = (v) => plotH - (v / 255) * (plotH - 4);
    if (target) band(ctx, 0, w, y(target[0]), y(target[1]), "rgba(60,207,122,0.15)");
    ctx.strokeStyle = "#2a353e";
    [64, 128, 192].forEach((v) => {
      ctx.beginPath(); ctx.moveTo(0, y(v)); ctx.lineTo(w, y(v)); ctx.stroke();
    });
    if (prof) {
      [["r", "#ff6b6b"], ["g", "#5fd38a"], ["b", "#5aa9ff"]].forEach(([k, color]) => {
        const data = prof[k];
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        data.forEach((v, i) => {
          const px = (i / (data.length - 1)) * w;
          i ? ctx.lineTo(px, y(v)) : ctx.moveTo(px, y(v));
        });
        ctx.stroke();
      });
    }
    ctx.fillStyle = "#8a9aa6";
    ctx.fillText("0", 2, h - 3);
    if (prof) ctx.fillText(String(prof.sensor_px), w - 30, h - 3);
    ctx.fillText("255", 2, 10);
  }

  return { hist, profile };
})();
