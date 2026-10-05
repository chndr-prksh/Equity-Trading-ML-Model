// Minimal canvas charts (no dependencies): a candlestick chart with overlays and a multi-line chart.
(function () {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function setup(canvas, height) {
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || canvas.parentElement.clientWidth || 600;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.height = height + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.font = "11px system-ui, sans-serif";
    return { ctx, width, height };
  }

  function niceTicks(lo, hi, n) {
    const span = hi - lo || 1;
    const step0 = Math.pow(10, Math.floor(Math.log10(span / n)));
    const step = [1, 2, 5, 10].map((m) => m * step0).find((s) => span / s <= n) || step0 * 10;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
    return out;
  }

  const fmt = (v) => (Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toLocaleString(undefined, { maximumFractionDigits: 2 }));

  // opts: {d,o,h,l,c,v, lines:[{values,color,label}], levels:[{value,color,label}], markers:[{date,type:'in'|'out'}], onHover}
  function candles(canvas, opts) {
    const H = 380, pad = { l: 8, r: 58, t: 10, b: 20 }, volH = 56;
    const n = opts.d.length;
    let hover = -1;
    function draw() {
      const { ctx, width } = setup(canvas, H);
      const plotW = width - pad.l - pad.r, priceH = H - pad.t - pad.b - volH - 8;
      const step = plotW / n, bw = Math.max(1, Math.min(9, step * 0.7));
      let lo = Infinity, hi = -Infinity;
      for (let i = 0; i < n; i++) { if (opts.l[i] != null) lo = Math.min(lo, opts.l[i]); if (opts.h[i] != null) hi = Math.max(hi, opts.h[i]); }
      for (const ln of opts.lines || []) for (const v of ln.values) if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      for (const lv of opts.levels || []) if (lv.value != null && lv.value > lo * 0.8 && lv.value < hi * 1.2) { lo = Math.min(lo, lv.value); hi = Math.max(hi, lv.value); }
      const m = (hi - lo) * 0.04; lo -= m; hi += m;
      const x = (i) => pad.l + step * (i + 0.5);
      const y = (v) => pad.t + priceH * (1 - (v - lo) / (hi - lo));
      ctx.clearRect(0, 0, width, H);
      ctx.strokeStyle = css("--line"); ctx.fillStyle = css("--muted"); ctx.lineWidth = 1;
      for (const t of niceTicks(lo, hi, 6)) {
        ctx.beginPath(); ctx.moveTo(pad.l, y(t)); ctx.lineTo(width - pad.r, y(t)); ctx.stroke();
        ctx.fillText(fmt(t), width - pad.r + 6, y(t) + 4);
      }
      let lastMonth = "";
      for (let i = 0; i < n; i++) {
        const mth = opts.d[i].slice(0, 7);
        if (mth !== lastMonth && i > 0 && (n < 140 || mth.slice(5) % 2 === 1)) ctx.fillText(mth, x(i) - 14, H - 5);
        lastMonth = mth;
      }
      const vmax = Math.max(1, ...opts.v);
      const up = css("--up"), down = css("--down");
      for (let i = 0; i < n; i++) {
        if (opts.c[i] == null) continue;
        const rising = opts.c[i] >= opts.o[i];
        ctx.strokeStyle = ctx.fillStyle = rising ? up : down;
        ctx.beginPath(); ctx.moveTo(x(i), y(opts.h[i])); ctx.lineTo(x(i), y(opts.l[i])); ctx.stroke();
        const top = y(Math.max(opts.o[i], opts.c[i])), bot = y(Math.min(opts.o[i], opts.c[i]));
        ctx.fillRect(x(i) - bw / 2, top, bw, Math.max(1, bot - top));
        ctx.globalAlpha = 0.35;
        const vh = volH * opts.v[i] / vmax;
        ctx.fillRect(x(i) - bw / 2, H - pad.b - vh, bw, vh);
        ctx.globalAlpha = 1;
      }
      for (const ln of opts.lines || []) {
        ctx.strokeStyle = ln.color; ctx.lineWidth = 1.5; ctx.beginPath();
        let started = false;
        ln.values.forEach((v, i) => { if (v == null) return; started ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)); started = true; });
        ctx.stroke();
      }
      ctx.lineWidth = 1;
      for (const lv of opts.levels || []) {
        if (lv.value == null || lv.value < lo || lv.value > hi) continue;
        ctx.strokeStyle = ctx.fillStyle = lv.color; ctx.setLineDash([5, 4]);
        ctx.beginPath(); ctx.moveTo(pad.l, y(lv.value)); ctx.lineTo(width - pad.r, y(lv.value)); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillText(lv.label, pad.l + 4, y(lv.value) - 4);
      }
      for (const mk of opts.markers || []) {
        const i = opts.d.indexOf(mk.date);
        if (i < 0) continue;
        const isIn = mk.type === "in";
        ctx.fillStyle = isIn ? css("--buy") : css("--exit");
        const py = isIn ? y(opts.l[i]) + 12 : y(opts.h[i]) - 12, s = isIn ? -1 : 1;
        ctx.beginPath(); ctx.moveTo(x(i), py + s * 7); ctx.lineTo(x(i) - 6, py - s * 4); ctx.lineTo(x(i) + 6, py - s * 4); ctx.closePath(); ctx.fill();
      }
      if (hover >= 0) {
        ctx.strokeStyle = css("--muted"); ctx.setLineDash([2, 3]);
        ctx.beginPath(); ctx.moveTo(x(hover), pad.t); ctx.lineTo(x(hover), H - pad.b); ctx.stroke(); ctx.setLineDash([]);
      }
      canvas._geom = { step, left: pad.l };
    }
    function move(ev) {
      const r = canvas.getBoundingClientRect(), g = canvas._geom;
      const i = Math.max(0, Math.min(n - 1, Math.floor((ev.clientX - r.left - g.left) / g.step)));
      if (i !== hover) { hover = i; draw(); opts.onHover && opts.onHover(i); }
    }
    canvas.onmousemove = move;
    canvas.ontouchmove = (e) => move(e.touches[0]);
    canvas.onmouseleave = () => { hover = -1; draw(); opts.onHover && opts.onHover(n - 1); };
    draw();
    return draw;
  }

  // opts: {x:[labels], series:[{values,color,label}], height, format, band:{lo,hi}}
  function lines(canvas, opts) {
    const H = opts.height || 220, pad = { l: 8, r: 52, t: 10, b: 20 };
    const n = opts.x.length, f = opts.format || fmt;
    let hover = -1;
    function draw() {
      const { ctx, width } = setup(canvas, H);
      const plotW = width - pad.l - pad.r, plotH = H - pad.t - pad.b;
      let lo = Infinity, hi = -Infinity;
      for (const s of opts.series) for (const v of s.values) if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      if (opts.band) { lo = Math.min(lo, opts.band.lo); hi = Math.max(hi, opts.band.hi); }
      const m = (hi - lo) * 0.05 || 1; lo -= m; hi += m;
      const x = (i) => pad.l + plotW * (n > 1 ? i / (n - 1) : 0.5), y = (v) => pad.t + plotH * (1 - (v - lo) / (hi - lo));
      ctx.clearRect(0, 0, width, H);
      ctx.strokeStyle = css("--line"); ctx.fillStyle = css("--muted");
      for (const t of niceTicks(lo, hi, 5)) {
        ctx.beginPath(); ctx.moveTo(pad.l, y(t)); ctx.lineTo(width - pad.r, y(t)); ctx.stroke();
        ctx.fillText(f(t), width - pad.r + 6, y(t) + 4);
      }
      const every = Math.max(1, Math.floor(n / 6));
      for (let i = 0; i < n; i += every) ctx.fillText(opts.x[i].slice(0, 7), Math.min(x(i), width - pad.r - 40), H - 5);
      for (const s of opts.series) {
        ctx.strokeStyle = s.color; ctx.lineWidth = 1.8; ctx.beginPath();
        let started = false;
        s.values.forEach((v, i) => { if (v == null) return; started ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)); started = true; });
        ctx.stroke();
      }
      ctx.lineWidth = 1;
      if (hover >= 0) {
        ctx.strokeStyle = css("--muted"); ctx.setLineDash([2, 3]);
        ctx.beginPath(); ctx.moveTo(x(hover), pad.t); ctx.lineTo(x(hover), H - pad.b); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = css("--ink");
        const text = opts.x[hover] + "   " + opts.series.map((s) => s.label + " " + (s.values[hover] == null ? "–" : f(s.values[hover]))).join("   ");
        ctx.fillText(text, pad.l + 4, pad.t + 10);
      }
      canvas._geom = { plotW, left: pad.l };
    }
    canvas.onmousemove = (ev) => {
      const r = canvas.getBoundingClientRect(), g = canvas._geom;
      const i = Math.max(0, Math.min(n - 1, Math.round((ev.clientX - r.left - g.left) / g.plotW * (n - 1))));
      if (i !== hover) { hover = i; draw(); }
    };
    canvas.onmouseleave = () => { hover = -1; draw(); };
    draw();
    return draw;
  }

  window.Charts = { candles, lines, css };
})();
