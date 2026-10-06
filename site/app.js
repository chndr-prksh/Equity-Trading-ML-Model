/* Ledgerline front end: a static page that reads the JSON written by tracker/publish.py. */
(function () {
  "use strict";
  const MARKETS = { us: "US", nse: "NSE" };
  const SIGNALS = {
    BUY: "New entry signal on the latest close",
    HOLD: "Inside an open system trade",
    EXIT: "Exit signal: stop hit, or closed below its 50-day average",
    SELL: "Trend breakdown: fell below its 50-day average with the 50-day under the 200-day",
    WATCH: "A leader within 5% of its breakout level",
    AVOID: "In a downtrend",
    NR: "Not rated: too illiquid, too cheap or too recently listed",
  };
  const CHECKS = [
    "Price above the 150-day and 200-day averages",
    "150-day average above the 200-day",
    "200-day average rising for a month",
    "50-day average above the 150-day and 200-day",
    "Price above the 50-day average",
    "At least 25% above the 52-week low",
    "Within 25% of the 52-week high",
    "Relative strength rating of 70 or more",
  ];
  const app = document.getElementById("app");
  const mount = (...nodes) => app.replaceChildren(...nodes.flat().filter(Boolean));
  const cache = {};
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };

  // ---------- helpers
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
    return el;
  }
  const slug = (s) => s.replace(/[^A-Za-z0-9_-]/g, "_");
  const num = (v, d = 2) => (v == null ? "–" : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (v, d = 1) => (v == null ? "–" : (v > 0 ? "+" : "") + (v * 100).toFixed(d) + "%");
  const pctEl = (v, d) => h("span", { class: v > 0 ? "up" : v < 0 ? "down" : "" }, pct(v, d));
  function compact(v, sym) {
    if (v == null) return "–";
    if (sym === "₹") {   // Indian convention: crore (1e7) and lakh crore (1e12)
      const cr = v / 1e7;
      return cr >= 1e5 ? "₹" + (cr / 1e5).toFixed(2) + "L Cr" : "₹" + cr.toLocaleString("en-IN", { maximumFractionDigits: cr >= 100 ? 0 : 1 }) + " Cr";
    }
    const units = [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]];
    for (const [n, u] of units) if (Math.abs(v) >= n) return sym + (v / n).toFixed(v / n >= 100 ? 0 : 1) + u;
    return sym + v.toFixed(0);
  }
  const sigEl = (s) => h("span", { class: "sig " + (s || "none"), title: SIGNALS[s] || "No signal" }, s === "NR" ? "n/r" : s || "–");
  const link = (market, view, arg) => "#/" + market + "/" + view + (arg ? "/" + encodeURIComponent(arg) : "");

  async function load(market, name) {
    const key = market + "/" + name;
    if (!cache[key]) {
      cache[key] = fetch("data/" + key + ".json", { cache: "no-cache" }).then((r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "not published yet" : "HTTP " + r.status);
        return r.json();
      });
      cache[key].catch(() => delete cache[key]);
    }
    return cache[key];
  }
  async function screener(market) {
    const s = await load(market, "screener");
    if (!s.index) {
      s.index = Object.fromEntries(s.cols.map((c, i) => [c, i]));
      s.bySymbol = new Map(s.rows.map((r) => [r[0], r]));
    }
    return s;
  }
  function table(cols, rows, onRow) {
    return h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.num ? "num" : "" }, c.label)))),
      h("tbody", {}, rows.map((r) => h("tr", { class: onRow ? "row" : "", onclick: onRow ? () => onRow(r) : null },
        cols.map((c) => h("td", { class: (c.num ? "num " : "") + (c.cls || "") }, c.cell(r))))))));
  }
  function staleWarning(sum) {
    const age = (Date.now() - new Date(sum.asof + "T00:00:00Z")) / 864e5;
    return age > 5 ? h("div", { class: "warn" }, `This market's data is from ${sum.asof}. The daily update has not run or its source failed; signals below may be out of date.`) : null;
  }

  // ---------- Today
  async function overview(market) {
    const [sum, scr] = await Promise.all([load(market, "summary"), screener(market)]);
    const I = scr.index, c = sum.counts, reg = sum.regime, cur = sum.market.symbol;
    const go = (sym) => (location.hash = link(market, "stock", sym));
    const regimeText = {
      uptrend: `${reg.benchmark} is above its 50-day and 200-day averages. New buy signals are active.`,
      caution: `${reg.benchmark} is between its 50-day and 200-day averages. New buy signals are active, but the backdrop is mixed.`,
      downtrend: `${reg.benchmark} is below its 50-day and 200-day averages. New buy signals are paused until it recovers; exits and sells still fire.`,
    }[reg.state];
    const pick = (sig, sortCol, n) => scr.rows.filter((r) => r[I.signal] === sig).sort((a, b) => (b[I[sortCol]] ?? -1) - (a[I[sortCol]] ?? -1)).slice(0, n);
    const cols = (extra) => [
      { label: "Stock", cell: (r) => h("b", {}, r[I.symbol]) },
      { label: "Name", cls: "name", cell: (r) => r[I.name] },
      { label: "Close", num: 1, cell: (r) => num(r[I.close]) },
      { label: "Day", num: 1, cell: (r) => pctEl(r[I.chg]) },
      ...extra,
      { label: "Mkt cap", num: 1, cell: (r) => compact(r[I.mcap], cur) },
    ];
    const section = (title, sig, note, extra, sortCol) => {
      const rows = pick(sig, sortCol, 12), total = c[sig];
      return h("div", { class: "card" }, h("h2", {}, sigEl(sig), " ", title, h("span", { class: "muted small" }, `  ${total}`)),
        h("p", { class: "muted small" }, note),
        rows.length ? table(cols(extra), rows, (r) => go(r[I.symbol])) : h("p", { class: "muted" }, "None today."),
        total > rows.length ? h("a", { class: "small", href: link(market, "screener") + "?signal=" + sig }, `See all ${total} →`) : null);
    };
    const bt = sum.backtest.trades.all;
    mount(
      staleWarning(sum),
      h("div", { class: "head" }, h("h1", {}, sum.market.name), h("span", { class: "muted" }, `after the close of ${sum.asof} · ${c.rated.toLocaleString()} stocks rated of ${c.tracked.toLocaleString()} tracked`)),
      h("div", { class: "grid" },
        h("div", { class: "card banner " + reg.state },
          h("h2", {}, "Market: " + reg.state[0].toUpperCase() + reg.state.slice(1)),
          h("p", {}, regimeText),
          h("p", { class: "muted small" }, `${reg.benchmark} ${num(reg.close)} (`, pctEl(reg.chg), `) · 50-day ${num(reg.sma50)} · 200-day ${num(reg.sma200)}`)),
        h("div", { class: "tiles" }, ["BUY", "EXIT", "SELL", "HOLD", "WATCH"].map((s) =>
          h("a", { class: "tile", href: link(market, "screener") + "?signal=" + s, title: SIGNALS[s] }, h("b", {}, c[s]), sigEl(s)))),
        h("div", { class: "grid g2" },
          section("New buy signals", "BUY", "Leaders that broke out or bounced off their 20-day average today. Ranked by relative strength; the stop is where the idea is wrong.",
            [{ label: "Setup", cell: (r) => r[I.setup] }, { label: "RS", num: 1, cell: (r) => r[I.rs] }, { label: "Stop", num: 1, cell: (r) => num(r[I.stop]) }], "rs"),
          section("Exit signals", "EXIT", "Open trades that hit their stop or closed below the 50-day average today.",
            [{ label: "Entry", num: 1, cell: (r) => num(r[I.entry]) }, { label: "Result", num: 1, cell: (r) => pctEl(r[I.pnl]) }, { label: "Days", num: 1, cell: (r) => r[I.days] }], "pnl"),
          section("Trend breakdowns", "SELL", "Stocks that just dropped into a downtrend. A warning for holders, not a short signal.",
            [{ label: "3 months", num: 1, cell: (r) => pctEl(r[I.ret_3m]) }, { label: "From high", num: 1, cell: (r) => pctEl(r[I.from_high]) }], "traded"),
          section("Close to a breakout", "WATCH", "Leaders passing all eight trend checks, within 5% of their 50-day high.",
            [{ label: "RS", num: 1, cell: (r) => r[I.rs] }, { label: "Breakout at", num: 1, cell: (r) => num(r[I.pivot]) }], "rs")),
        h("div", { class: "grid g2" },
          h("div", { class: "card" }, h("h2", {}, "Breadth"), h("p", { class: "muted small" }, "Share of rated stocks above their 50-day and 200-day averages."),
            h("canvas", { id: "breadth" }), h("div", { class: "legend" }, h("span", {}, h("i", { style: "background:var(--accent)" }), "above 50-day"), h("span", {}, h("i", { style: "background:var(--exit)" }), "above 200-day"))),
          h("div", { class: "card" }, h("h2", {}, "How these rules have done"),
            h("p", {}, `Backtest since ${sum.backtest.from}: ${bt.n.toLocaleString()} closed trades, ${pct(bt.win_rate, 0).replace("+", "")} winners, average `, pctEl(bt.avg, 2), ` per trade after costs, versus `, pctEl(bt.bench_avg, 2), ` for ${reg.benchmark} over the same days.`),
            h("p", { class: "muted small" }, "That backtest only covers stocks still listed today, which flatters it. The live record started on " + sum.live.since + "."),
            h("a", { href: link(market, "record") }, "Full track record →"))),
        h("div", { class: "card" }, h("h2", {}, "Sectors"), h("p", { class: "muted small" }, "Median of rated stocks in each group, strongest relative strength first."),
          table([
            { label: "Sector", cell: (s) => h("a", { href: link(market, "screener") + "?sector=" + encodeURIComponent(s.name) }, s.name) },
            { label: "Stocks", num: 1, cell: (s) => s.n }, { label: "Total mkt cap", num: 1, cell: (s) => (s.mcap ? compact(s.mcap, cur) : "–") }, { label: "Median RS", num: 1, cell: (s) => s.rs ?? "–" },
            { label: "Day", num: 1, cell: (s) => pctEl(s.chg) }, { label: "1 month", num: 1, cell: (s) => pctEl(s.ret_1m) },
            { label: "3 months", num: 1, cell: (s) => pctEl(s.ret_3m) }, { label: "Above 50-day", num: 1, cell: (s) => s.above50 + "%" },
            { label: "Leaders", num: 1, cell: (s) => s.leaders },
          ], sum.sectors))));
    const b = sum.breadth;
    Charts.lines(document.getElementById("breadth"), { x: b.dates, format: (v) => v.toFixed(0) + "%", band: { lo: 0, hi: 100 },
      series: [{ label: "above 50-day", values: b.above50, color: Charts.css("--accent") }, { label: "above 200-day", values: b.above200, color: Charts.css("--exit") }] });
  }

  // ---------- Screener
  const PRESETS = {
    "New buys": (r, I) => r[I.signal] === "BUY",
    "Exits & sells": (r, I) => r[I.signal] === "EXIT" || r[I.signal] === "SELL",
    "Leaders (8 of 8)": (r, I) => r[I.tt] === 8,
    "Near breakout": (r, I) => r[I.signal] === "WATCH",
    "New 52-week highs": (r, I) => r[I.rated] && r[I.from_high] != null && r[I.from_high] >= -0.005,
    "Volume surge": (r, I) => r[I.rated] && r[I.vol_x] >= 2 && r[I.chg] > 0,
  };
  const filt = { q: "", signal: "", sector: "", preset: "", size: "", rs: 0, rated: true, sort: "score", dir: -1, page: 0 };
  // US: the usual dollar bands. NSE: SEBI's rank bands (top 100 large, next 150 mid, rest small).
  function sizeOf(market, scr) {
    const I = scr.index;
    if (!scr.size) {
      scr.size = new Map();
      const ranked = scr.rows.filter((r) => r[I.mcap] > 0).sort((a, b) => b[I.mcap] - a[I.mcap]);
      ranked.forEach((r, i) => scr.size.set(r[0], market === "nse" ? (i < 100 ? "Large" : i < 250 ? "Mid" : "Small")
        : r[I.mcap] >= 1e10 ? "Large" : r[I.mcap] >= 2e9 ? "Mid" : "Small"));
    }
    return scr.size;
  }
  async function screenerView(market, _arg, query) {
    const scr = await screener(market), sum = await load(market, "summary");
    const I = scr.index;
    if (query.has("signal") || query.has("sector")) Object.assign(filt, { signal: query.get("signal") || "", sector: query.get("sector") || "", preset: "", size: "", q: "", rs: 0, page: 0 });
    const sectors = [...new Set(scr.rows.map((r) => r[I.sector]))].sort();
    const size = sizeOf(market, scr);
    const body = h("div", {});
    const COLS = [
      ["symbol", "Stock"], ["name", "Name"], ["signal", "Signal"], ["close", "Close", 1], ["chg", "Day", 1], ["score", "Score", 1], ["rs", "RS", 1],
      ["tt", "Trend", 1], ["from_high", "From high", 1], ["ret_1m", "1 mo", 1], ["ret_3m", "3 mo", 1], ["vol_x", "Vol ×", 1], ["mcap", "Mkt cap", 1], ["traded", "Traded/day", 1], ["sector", "Sector"],
    ];
    function render() {
      const q = filt.q.trim().toUpperCase();
      let rows = scr.rows.filter((r) =>
        (!filt.rated || r[I.rated] || q) && (!filt.size || size.get(r[0]) === filt.size) && (!filt.signal || r[I.signal] === filt.signal) && (!filt.sector || r[I.sector] === filt.sector) &&
        (!filt.rs || (r[I.rs] ?? 0) >= filt.rs) && (!filt.preset || PRESETS[filt.preset](r, I)) &&
        (!q || r[I.symbol].startsWith(q) || r[I.name].toUpperCase().includes(q)));
      const k = I[filt.sort];
      rows.sort((a, b) => {
        const x = a[k], y = b[k];
        if (x == null || x === "") return 1; if (y == null || y === "") return -1;
        return (typeof x === "string" ? x.localeCompare(y) : x - y) * filt.dir;
      });
      const per = 50, pages = Math.max(1, Math.ceil(rows.length / per));
      filt.page = Math.min(filt.page, pages - 1);
      const cell = (r, c) => {
        const v = r[I[c]];
        if (c === "symbol") return h("b", {}, v);
        if (c === "signal") return sigEl(v);
        if (c === "close") return num(v);
        if (["chg", "from_high", "ret_1m", "ret_3m"].includes(c)) return pctEl(v);
        if (c === "tt") return v == null ? "–" : v + "/8";
        if (c === "vol_x") return v == null ? "–" : v.toFixed(1);
        if (c === "traded" || c === "mcap") return compact(v, sum.market.symbol);
        return v ?? "–";
      };
      body.replaceChildren(
        h("p", { class: "muted small" }, `${rows.length.toLocaleString()} stocks`),
        h("div", { class: "scroll" }, h("table", {},
          h("thead", {}, h("tr", {}, COLS.map(([c, label, isNum]) => h("th", { class: "sort" + (isNum ? " num" : ""), onclick: () => {
            if (filt.sort === c) filt.dir = -filt.dir; else { filt.sort = c; filt.dir = isNum ? -1 : 1; } render();
          } }, label + (filt.sort === c ? (filt.dir < 0 ? " ↓" : " ↑") : ""))))),
          h("tbody", {}, rows.slice(filt.page * per, (filt.page + 1) * per).map((r) => h("tr", { class: "row", onclick: () => (location.hash = link(market, "stock", r[I.symbol])) },
            COLS.map(([c, , isNum]) => h("td", { class: (isNum ? "num" : "") + (c === "name" || c === "sector" ? " name" : "") }, cell(r, c)))))))),
        h("div", { class: "pager" },
          h("button", { class: "btn", disabled: filt.page === 0, onclick: () => { filt.page--; render(); } }, "← Prev"),
          h("span", { class: "muted small" }, `Page ${filt.page + 1} of ${pages}`),
          h("button", { class: "btn", disabled: filt.page >= pages - 1, onclick: () => { filt.page++; render(); } }, "Next →")));
    }
    const set = (patch) => { Object.assign(filt, patch, { page: 0 }); draw(); };
    function draw() {
      mount(staleWarning(sum),
        h("div", { class: "head" }, h("h1", {}, sum.market.name + " screener"), h("span", { class: "muted" }, "as of " + scr.asof)),
        h("div", { class: "card" },
          h("div", { class: "chips", style: "margin-bottom:10px" }, Object.keys(PRESETS).map((p) =>
            h("button", { class: "chip" + (filt.preset === p ? " on" : ""), onclick: () => set({ preset: filt.preset === p ? "" : p, signal: "" }) }, p))),
          h("div", { class: "filters" },
            h("input", { type: "search", placeholder: "Symbol or name", value: filt.q, oninput: (e) => { filt.q = e.target.value; filt.page = 0; render(); } }),
            h("select", { onchange: (e) => set({ signal: e.target.value, preset: "" }) }, h("option", { value: "" }, "Any signal"),
              Object.keys(SIGNALS).map((s) => h("option", { value: s, selected: filt.signal === s }, s === "NR" ? "Not rated" : s))),
            h("select", { onchange: (e) => set({ sector: e.target.value }) }, h("option", { value: "" }, "All sectors"),
              sectors.map((s) => h("option", { value: s, selected: filt.sector === s }, s))),
            h("select", { title: market === "nse" ? "By market-cap rank: top 100, next 150, the rest" : "Large $10B+, mid $2–10B, small under $2B", onchange: (e) => set({ size: e.target.value }) },
              h("option", { value: "" }, "Any size"), ["Large", "Mid", "Small"].map((v) => h("option", { value: v, selected: filt.size === v }, v + " cap"))),
            h("select", { onchange: (e) => set({ rs: +e.target.value }) }, [0, 70, 80, 90].map((v) => h("option", { value: v, selected: filt.rs === v }, v ? "RS " + v + "+" : "Any RS"))),
            h("label", { class: "small" }, h("input", { type: "checkbox", checked: filt.rated, onchange: (e) => set({ rated: e.target.checked }) }), " Rated stocks only")),
          body));
      render();
    }
    draw();
  }

  // ---------- Stock
  async function stock(market, sym) {
    const [sum, scr] = await Promise.all([load(market, "summary"), screener(market)]);
    const I = scr.index, row = scr.bySymbol.get(sym);
    if (!row) { mount(h("p", { class: "pad" }, `${sym} is not in the ${MARKETS[market]} universe. `, h("a", { href: link(market, "screener") }, "Open the screener"))); return; }
    const cur = sum.market.symbol, sig = row[I.signal];
    let det = null;
    try { det = await load(market, "s/" + slug(sym)); } catch (e) { /* unrated stocks have no chart file */ }
    const list = store.get("st.list", []), inList = list.some((x) => x.m === market && x.s === sym);
    const explain = {
      BUY: `Entry signal (${row[I.setup]}) on the close of ${sum.asof}. The system assumes a fill at the next open and a stop near ${cur}${num(row[I.stop])}.`,
      HOLD: `System trade opened ${row[I.days]} sessions ago at ${cur}${num(row[I.entry])}. It exits if price trades at the stop (${cur}${num(row[I.stop])}) or closes below the 50-day average.`,
      EXIT: `Exit signal on ${sum.asof}. The system trade from ${cur}${num(row[I.entry])} is closed.`,
      SELL: "Dropped into a downtrend today: closed below its 50-day average, which is below the 200-day.",
      WATCH: `Passes all eight trend checks and sits within 5% of its breakout level (${cur}${num(row[I.pivot])}). No entry signal yet.`,
      AVOID: "In a downtrend: price below the 50-day average and the 50-day below the 200-day.",
      NR: `Not rated. Signals need a year of history, a price of at least ${cur}${sum.market.min_price} and about ${compact(sum.market.min_traded_value, cur)} traded per day.`,
      "": "No active signal. See the trend checks below.",
    }[sig];
    const info = h("div", { class: "muted small", id: "bar-info" });
    const page = [
      staleWarning(sum),
      h("div", { class: "head" }, h("h1", {}, sym), h("span", { class: "muted" }, row[I.name] + " · " + row[I.sector] + (row[I.mcap] ? " · " + compact(row[I.mcap], cur) + " market cap" : "")),
        h("span", { class: "px" }, cur + num(row[I.close])), pctEl(row[I.chg], 2), sigEl(sig),
        h("button", { class: "btn" + (inList ? "" : " primary"), onclick: () => {
          const next = inList ? list.filter((x) => !(x.m === market && x.s === sym)) : [...list, { m: market, s: sym, px: null, qty: null }];
          store.set("st.list", next); stock(market, sym);
        } }, inList ? "Remove from my list" : "Add to my list")),
      h("div", { class: "card banner" }, h("p", {}, explain), row[I.rated] ? h("p", { class: "muted small" },
        `${row[I.trend]} · score ${row[I.score]} · relative strength ${row[I.rs]} of 99 · ${row[I.tt]} of 8 trend checks · `, pctEl(row[I.from_high]), " from 52-week high") : null),
    ];
    if (det) {
      page.push(
        h("div", { class: "card", style: "margin-top:14px" }, h("canvas", { id: "chart" }), info,
          h("div", { class: "legend" }, h("span", {}, h("i", { style: "background:var(--accent)" }), "50-day"), h("span", {}, h("i", { style: "background:var(--exit)" }), "200-day"),
            h("span", {}, "▲ system entry"), h("span", {}, "▼ system exit"))),
        h("div", { class: "grid g2", style: "margin-top:14px" },
          h("div", { class: "card" }, h("h2", {}, "Trend checks"), h("ul", { class: "checks" }, CHECKS.map((t, i) => h("li", { class: det.checks[i] ? "ok" : "" }, t)))),
          h("div", { class: "card" }, h("h2", {}, "Levels"), h("dl", { class: "kv" },
            [["Breakout level (50-day high)", det.levels.pivot], ["Stop for this trade", sig === "HOLD" || sig === "BUY" ? row[I.stop] : null], ["50-day average", det.sma50.at(-1)],
              ["200-day average", det.sma200.at(-1)], ["52-week high", det.levels.hi52], ["52-week low", det.levels.lo52], ["Average daily range (ATR)", det.levels.atr]]
              .filter(([, v]) => v != null).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, cur + num(v))]),
            row[I.mcap] ? [h("dt", {}, "Market cap"), h("dd", {}, compact(row[I.mcap], cur))] : null, [h("dt", {}, "Traded per day"), h("dd", {}, compact(row[I.traded], cur))]))),
        h("div", { class: "card", style: "margin-top:14px" }, h("h2", {}, "System trades in this stock"),
          det.trades.length ? table([
            { label: "Setup", cell: (t) => t.setup }, { label: "Signal", cell: (t) => t.signal }, { label: "Entry", num: 1, cell: (t) => num(t.entry_px) },
            { label: "Stop", num: 1, cell: (t) => num(t.stop) }, { label: "Exit", cell: (t) => t.exit || (t.reason ? "next open" : "open") },
            { label: "Exit price", num: 1, cell: (t) => num(t.exit_px) }, { label: "Why", cell: (t) => ({ stop: "stop hit", trend: "below 50-day", halted: "stopped trading" }[t.reason] || "") },
            { label: "Result", num: 1, cell: (t) => pctEl(t.ret) },
          ], det.trades.slice().reverse()) : h("p", { class: "muted" }, "No entry signals in the last five years."),
          h("p", { class: "muted small", style: "margin-top:8px" }, "Replayed from history with today's rules. Entries fill at the next open; results are after assumed costs.")));
    }
    mount(...page.filter(Boolean));
    if (det) {
      const n = Math.min(det.d.length, 250), cut = (a) => a.slice(-n);
      const showBar = (i) => { const k = det.d.length - n + i; info.textContent = `${det.d[k]}  O ${num(det.o[k])}  H ${num(det.h[k])}  L ${num(det.l[k])}  C ${num(det.c[k])}  Vol ${det.v[k].toLocaleString()}`; };
      Charts.candles(document.getElementById("chart"), {
        d: cut(det.d), o: cut(det.o), h: cut(det.h), l: cut(det.l), c: cut(det.c), v: cut(det.v),
        lines: [{ values: cut(det.sma50), color: Charts.css("--accent") }, { values: cut(det.sma200), color: Charts.css("--exit") }],
        levels: [sig === "HOLD" || sig === "BUY" ? { value: row[I.stop], color: Charts.css("--sell"), label: "stop" } : null,
          sig === "WATCH" ? { value: det.levels.pivot, color: Charts.css("--watch"), label: "breakout" } : null].filter(Boolean),
        markers: det.trades.flatMap((t) => [{ date: t.entry, type: "in" }, t.exit ? { date: t.exit, type: "out" } : null]).filter(Boolean),
        onHover: showBar,
      });
      showBar(n - 1);
    }
  }

  // ---------- My list
  async function myList() {
    const list = store.get("st.list", []);
    const markets = [...new Set(list.map((x) => x.m))];
    const data = {};
    await Promise.all(markets.map(async (m) => { try { data[m] = { scr: await screener(m), sum: await load(m, "summary") }; } catch (e) { /* market not published */ } }));
    const save = (next) => { store.set("st.list", next); myList(); };
    const rows = list.map((x) => {
      const d = data[x.m]; const r = d && d.scr.bySymbol.get(x.s); const I = d && d.scr.index;
      return { x, r, I, cur: d ? d.sum.market.symbol : "" };
    });
    const alerts = rows.filter(({ r, I }) => r && ["EXIT", "SELL"].includes(r[I.signal]));
    const file = h("input", { type: "file", accept: "application/json", hidden: true, onchange: async (e) => {
      try { const next = JSON.parse(await e.target.files[0].text()); if (Array.isArray(next)) save(next.filter((x) => x && MARKETS[x.m] && typeof x.s === "string")); } catch (err) { alert("That file is not a saved list."); }
    } });
    mount(
      h("div", { class: "head" }, h("h1", {}, "My list"), h("span", { class: "muted" }, "Stocks you follow or hold. Saved in this browser only.")),
      alerts.length ? h("div", { class: "warn" }, h("b", {}, `${alerts.length} of your stocks triggered today: `), alerts.map(({ x, r, I }) => `${x.s} (${r[I.signal]})`).join(", ")) : null,
      h("div", { class: "card" },
        list.length ? table([
          { label: "Stock", cell: ({ x }) => h("a", { href: link(x.m, "stock", x.s) }, h("b", {}, x.s)) },
          { label: "Market", cell: ({ x }) => MARKETS[x.m] },
          { label: "Signal", cell: ({ r, I }) => (r ? sigEl(r[I.signal]) : "no data") },
          { label: "Close", num: 1, cell: ({ r, I, cur }) => (r ? cur + num(r[I.close]) : "–") },
          { label: "Day", num: 1, cell: ({ r, I }) => (r ? pctEl(r[I.chg]) : "–") },
          { label: "Trend", cell: ({ r, I }) => (r ? r[I.trend] || "–" : "–") },
          { label: "RS", num: 1, cell: ({ r, I }) => (r ? r[I.rs] ?? "–" : "–") },
          { label: "Mkt cap", num: 1, cell: ({ r, I, cur }) => (r ? compact(r[I.mcap], cur) : "–") },
          { label: "System stop", num: 1, cell: ({ r, I, cur }) => (r && r[I.stop] != null ? cur + num(r[I.stop]) : "–") },
          { label: "Your price", num: 1, cell: ({ x }) => h("input", { type: "number", step: "any", min: "0", style: "width:92px", value: x.px ?? "", placeholder: "optional",
            onchange: (e) => { x.px = e.target.value ? +e.target.value : null; save(list); } }) },
          { label: "Your result", num: 1, cell: ({ x, r, I }) => (r && x.px ? pctEl(r[I.close] / x.px - 1) : "–") },
          { label: "", cell: ({ x }) => h("button", { class: "btn", title: "Remove", onclick: (e) => { e.stopPropagation(); save(list.filter((y) => y !== x)); } }, "✕") },
        ], rows) : h("p", { class: "muted" }, "Nothing here yet. Open any stock and choose “Add to my list”, or use the search box above."),
        h("div", { class: "filters", style: "margin-top:12px" },
          h("button", { class: "btn", onclick: () => {
            const a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(list, null, 1)], { type: "application/json" })), download: "ledgerline-list.json" }); a.click();
          } }, "Export"), h("button", { class: "btn", onclick: () => file.click() }, "Import"), file,
          h("span", { class: "muted small" }, "Enter the price you paid to see your own result next to the system's stop."))));
  }

  // ---------- Track record
  async function record(market) {
    const [sum, led] = await Promise.all([load(market, "summary"), load(market, "ledger")]);
    const t = sum.backtest.trades, pf = sum.backtest.portfolio, bench = sum.regime.benchmark;
    const statCols = [
      { label: "", cell: ([k]) => h("b", {}, k) }, { label: "Trades", num: 1, cell: ([, s]) => s.n.toLocaleString() },
      { label: "Winners", num: 1, cell: ([, s]) => (s.n ? (s.win_rate * 100).toFixed(0) + "%" : "–") }, { label: "Avg trade", num: 1, cell: ([, s]) => pctEl(s.avg, 2) },
      { label: bench + " same days", num: 1, cell: ([, s]) => pctEl(s.bench_avg, 2) }, { label: "Difference", num: 1, cell: ([, s]) => pctEl(s.excess, 2) },
      { label: "Avg win", num: 1, cell: ([, s]) => pctEl(s.avg_win) }, { label: "Avg loss", num: 1, cell: ([, s]) => pctEl(s.avg_loss) },
      { label: "Profit factor", num: 1, cell: ([, s]) => s.profit_factor ?? "–" }, { label: "Avg days", num: 1, cell: ([, s]) => s.avg_days ?? "–" },
    ];
    const live = sum.live.trades.all;
    mount(
      h("div", { class: "head" }, h("h1", {}, sum.market.name + " track record"), h("span", { class: "muted" }, "as of " + sum.asof)),
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", {}, "Live record since " + sum.live.since),
          h("p", {}, live.n ? `${live.n} trades opened and closed since launch: average ` : "No trade has been opened and closed since launch yet. ", live.n ? pctEl(live.avg, 2) : null,
            live.n ? ` per trade, against ` : null, live.n ? pctEl(live.bench_avg, 2) : null, live.n ? ` for ${bench}.` : null),
          h("p", { class: "muted small" }, `Every signal is appended to a log file and committed to the repository the day it fires, so it cannot be edited later. `,
            h("a", { href: `https://github.com/chndr-prksh/ledgerline/commits/ledger/ledger/${market}.csv` }, "See the commit history")),
          led.rows.length ? table([
            { label: "Date", cell: (r) => r[0] }, { label: "Stock", cell: (r) => h("a", { href: link(market, "stock", r[1]) }, r[1]) }, { label: "Signal", cell: (r) => sigEl(r[2]) },
            { label: "Detail", cell: (r) => r[3] }, { label: "Price", num: 1, cell: (r) => num(r[4]) }, { label: "Stop", num: 1, cell: (r) => num(r[5]) }, { label: "RS", num: 1, cell: (r) => r[6] ?? "–" },
          ], led.rows.slice().reverse().slice(0, 40)) : null,
          led.rows.length > 40 ? h("p", { class: "muted small", style: "margin-top:8px" }, `Latest 40 of ${sum.live.events.toLocaleString()} logged signals. `, h("a", { href: `https://github.com/chndr-prksh/ledgerline/blob/ledger/ledger/${market}.csv` }, "Full log (CSV)")) : null),
        h("div", { class: "card" }, h("h2", {}, "Backtest since " + sum.backtest.from),
          h("div", { class: "warn" }, "Read this before the numbers. The backtest replays today's rules over stocks that are still listed today, so companies that failed or were delisted are missing and results look better than they would have been. Two choices (exiting on a close below the 50-day average, and skipping stocks that swing more than 6% a day) were made after looking at this same history. Treat it as a description of the rules, not a promise."),
          pf ? h("div", {}, h("h3", {}, `Simulated portfolio: up to ${sum.rules.max_positions} equal positions, strongest first`),
            h("canvas", { id: "equity" }),
            h("div", { class: "legend" }, h("span", {}, h("i", { style: "background:var(--accent)" }), "rules"), h("span", {}, h("i", { style: "background:var(--muted)" }), bench)),
            table([{ label: "", cell: ([k]) => h("b", {}, k) }, { label: "Total", num: 1, cell: ([, m]) => pctEl(m.total) }, { label: "Per year", num: 1, cell: ([, m]) => pctEl(m.cagr) },
              { label: "Worst drawdown", num: 1, cell: ([, m]) => pctEl(m.max_drawdown) }], [["Rules", pf.metrics.strategy], [bench, pf.metrics.benchmark]]),
            h("p", { class: "muted small" }, `Average share of the portfolio invested: ${(pf.metrics.avg_exposure * 100).toFixed(0)}%. Cash earns nothing. Costs of ${(sum.market.round_trip_cost * 100).toFixed(1)}% per round trip.`)) : null,
          h("h3", {}, "Every signal, as separate trades"), table(statCols, [["All", t.all], ...Object.entries(t.by_setup), ...Object.entries(t.by_reason).map(([k, v]) => ["exit: " + k, v])]),
          h("h3", {}, "By year of entry"), table(statCols, Object.entries(t.by_year)))));
    if (pf) Charts.lines(document.getElementById("equity"), { x: pf.dates, height: 260, format: (v) => v.toFixed(2) + "×",
      series: [{ label: "rules", values: pf.strategy, color: Charts.css("--accent") }, { label: bench, values: pf.benchmark, color: Charts.css("--muted") }] });
  }

  // ---------- Method
  async function method(market) {
    const sum = await load(market, "summary"), r = sum.rules, m = sum.market, cur = m.symbol, adj = sum.adjustments;
    mount(
      h("div", { class: "head" }, h("h1", {}, "How it works"), h("span", { class: "muted" }, sum.market.name)),
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", {}, "What each signal means"), h("dl", { class: "kv" }, Object.entries(SIGNALS).flatMap(([k, v]) => [h("dt", {}, sigEl(k)), h("dd", { style: "text-align:left" }, v)]))),
        h("div", { class: "card" }, h("h2", {}, "The rules"),
          h("h3", {}, "1. Which stocks are rated"), h("p", {}, `At least ${r.min_bars} sessions of history, a price of ${cur}${m.min_price} or more, and a median of ${compact(m.min_traded_value, cur)} traded per day over ${r.liquidity_days} sessions. Today that is ${sum.counts.rated.toLocaleString()} of ${sum.counts.tracked.toLocaleString()} stocks.`),
          h("h3", {}, "2. Relative strength (RS)"), h("p", {}, "Each stock's return over the last 3, 6, 9 and 12 months, with the latest quarter counted twice, ranked 1–99 against every other rated stock in the market."),
          h("h3", {}, "3. Trend checks"), h("ul", { class: "checks" }, CHECKS.map((t) => h("li", { class: "ok" }, t))),
          h("h3", {}, "4. Buy"), h("p", {}, `All eight checks pass, the market is not in a downtrend, the stock's average daily range is at most ${r.max_atr_pct * 100}% of its price, and either (breakout) the stock closes above its prior ${r.pivot_days}-day high on ${r.breakout_vol_mult}× average volume, or (pullback) it touches its 20-day average and closes back above the previous day's high.`),
          h("h3", {}, "5. Exit"), h("p", {}, `A stop ${r.stop_atr}× the average daily range (ATR) below the entry, or the first close below the 50-day average. Entries and trend exits are assumed to fill at the next open.`),
          h("h3", {}, "6. Sell and avoid"), h("p", {}, "Sell marks the day a stock drops into a downtrend (below its 50-day average, with the 50-day under the 200-day). Avoid means it is still there."),
          h("h3", {}, "7. Market"), h("p", {}, `Uptrend, caution or downtrend from where ${sum.regime.benchmark} sits against its own 50-day and 200-day averages. Buys pause in a downtrend.`)),
        h("div", { class: "card" }, h("h2", {}, "Data"), h("dl", { class: "kv" },
          [["Prices", sum.sources.price_source], ["Stock list", sum.sources.universe_source], ["Benchmark", sum.regime.benchmark + " (" + sum.sources.benchmark_source + ")"],
            ["History from", sum.history_from], ["Last session", sum.asof], ["Generated", sum.generated.replace("T", " ").replace("Z", " UTC")],
            ["Split/bonus adjustments applied", adj.count || "handled by the source"],
            adj.audit ? ["Checked against NSE's MCP server", `${adj.audit.checked} stocks, ${adj.audit.issues.length} disagreements`] : null]
            .filter(Boolean).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))])),
          h("p", { class: "muted small", style: "margin-top:10px" }, "Prices are adjusted for splits and bonus issues but not for dividends, so levels match what you see on a chart. End-of-day only: nothing here updates during market hours.")),
        h("div", { class: "card" }, h("h2", {}, "What this is not"),
          h("p", {}, "It is a transparent rule set applied to public prices. It does not know your finances, it ignores earnings, news and valuation, and its backtest has not beaten the index in every market or year (see the track record). Use it to track and study stocks, not as advice."))));
  }

  // ---------- routing
  const VIEWS = { overview, screener: screenerView, stock, list: myList, record, method };
  async function route() {
    const [path, qs] = location.hash.replace(/^#\/?/, "").split("?");
    const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
    let market = MARKETS[parts[0]] ? parts.shift() : store.get("st.market", "us");
    const view = VIEWS[parts[0]] ? parts.shift() : "overview";
    store.set("st.market", market);
    document.querySelectorAll("#market-switch button").forEach((b) => b.classList.toggle("on", b.dataset.market === market));
    document.querySelectorAll("#nav a").forEach((a) => { a.href = link(market, a.dataset.view); a.classList.toggle("on", a.dataset.view === view); });
    try {
      await VIEWS[view](market, parts[0], new URLSearchParams(qs || ""));
    } catch (e) {
      mount(h("div", { class: "card" }, h("h2", {}, `No ${MARKETS[market]} data to show`),
        h("p", { class: "muted" }, `The ${MARKETS[market]} files could not be loaded (${e.message}). The daily job may not have published this market yet.`)));
    }
    window.scrollTo(0, 0);
  }
  document.querySelectorAll("#market-switch button").forEach((b) => b.addEventListener("click", () => {
    const parts = location.hash.replace(/^#\/?/, "").split("?")[0].split("/").filter(Boolean);
    if (MARKETS[parts[0]]) parts.shift();
    const view = VIEWS[parts[0]] && parts[0] !== "stock" ? parts[0] : "overview";
    location.hash = link(b.dataset.market, view);
  }));

  // global search across both markets
  const input = document.getElementById("jump-input"), results = document.getElementById("jump-results");
  input.addEventListener("input", async () => {
    const q = input.value.trim().toUpperCase();
    if (!q) { results.hidden = true; return; }
    const hits = [];
    for (const m of Object.keys(MARKETS)) {
      let s; try { s = await screener(m); } catch (e) { continue; }
      for (const r of s.rows) {
        const sym = r[0], rank = sym === q ? 0 : sym.startsWith(q) ? 1 : r[1].toUpperCase().includes(q) ? 2 : -1;
        if (rank >= 0) hits.push([rank, -(r[s.index.traded] || 0), m, r]);
      }
    }
    if (input.value.trim().toUpperCase() !== q) return;
    hits.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    results.replaceChildren(...(hits.length ? hits.slice(0, 12).map(([, , m, r]) =>
      h("a", { href: link(m, "stock", r[0]), onclick: () => { results.hidden = true; input.value = ""; } }, h("span", {}, h("b", {}, r[0]), " ", h("span", { class: "muted small" }, r[1])), h("span", { class: "muted small" }, MARKETS[m])))
      : [h("p", { class: "muted small", style: "padding:8px 10px;margin:0" }, "No match")]));
    results.hidden = false;
  });
  document.getElementById("jump").addEventListener("submit", (e) => { e.preventDefault(); const first = results.querySelector("a"); if (first) first.click(); });
  document.addEventListener("click", (e) => { if (!e.target.closest("#jump")) results.hidden = true; });
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => { clearTimeout(route.t); route.t = setTimeout(() => document.querySelector("canvas") && route(), 250); });
  route();
})();
