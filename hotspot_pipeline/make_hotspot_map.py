"""Draw the district hotspot map from a hotspot_eps*.csv produced by hotspot_dp.py.

Usage: python make_hotspot_map.py <hotspot csv>

Writes hotspot_map.html next to the csv: three small maps on one shared scale
(real intersection, synthetic table, published DP release) and a table of the
top districts. Only the third map is meant to leave the coordinator.
"""
import json
import sys
from pathlib import Path

import pandas as pd

from hotspot_dp import MIN_PUBLISHED_COUNT

# main island and Penghu; Kinmen and Lienchiang stay in the table but would shrink the map
LON_RANGE = (119.3, 122.1)
LAT_RANGE = (21.8, 25.4)
TOP_ROWS = 15

PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>毒駕熱區（鄉鎮層級）</title>
<style>
.viz-root {
  color-scheme: light;
  --surface-1: #fcfcfb; --text-primary: #0b0b0b; --text-secondary: #52514e;
  --series-1: #2a78d6; --ground: #d9d8d3; --rule: #e6e5e0;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    color-scheme: dark;
    --surface-1: #1a1a19; --text-primary: #ffffff; --text-secondary: #c3c2b7;
    --series-1: #3987e5; --ground: #4a4945; --rule: #33322f;
  }
}
:root[data-theme="dark"] .viz-root {
  color-scheme: dark;
  --surface-1: #1a1a19; --text-primary: #ffffff; --text-secondary: #c3c2b7;
  --series-1: #3987e5; --ground: #4a4945; --rule: #33322f;
}
body { margin: 0; }
.viz-root { background: var(--surface-1); color: var(--text-primary); font: 14px/1.5 system-ui, "Noto Sans TC", "Microsoft JhengHei", sans-serif; padding: 24px 16px 40px; min-height: 100vh; box-sizing: border-box; }
.wrap { max-width: 1040px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--text-secondary); margin: 0 0 20px; max-width: 70ch; }
.maps { display: flex; flex-wrap: wrap; gap: 16px; }
.panel { flex: 1 1 280px; min-width: 0; }
.panel h2 { font-size: 14px; margin: 0; }
.panel p { color: var(--text-secondary); font-size: 12px; margin: 2px 0 6px; }
svg { width: 100%; height: auto; display: block; }
.ground { fill: var(--ground); }
.bubble { fill: var(--series-1); fill-opacity: .78; stroke: var(--surface-1); stroke-width: 1.5; }
.bubble:hover, .bubble.on { fill-opacity: 1; }
.lab { fill: var(--text-primary); font-size: 11px; paint-order: stroke; stroke: var(--surface-1); stroke-width: 3px; }
.key { color: var(--text-secondary); font-size: 12px; margin: 10px 0 0; display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
.key svg { width: auto; height: 34px; }
.tip { position: fixed; pointer-events: none; background: var(--surface-1); color: var(--text-primary); border: 1px solid var(--rule); border-radius: 6px; padding: 6px 9px; font-size: 12px; box-shadow: 0 2px 10px rgba(0,0,0,.15); }
.tip b { display: block; }
.scroll { overflow-x: auto; margin-top: 28px; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
caption { text-align: left; font-weight: 600; padding-bottom: 6px; }
th, td { padding: 5px 10px; border-bottom: 1px solid var(--rule); text-align: right; white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--text-secondary); font-weight: 500; }
.note { color: var(--text-secondary); font-size: 12px; margin-top: 14px; max-width: 80ch; }
</style>
</head>
<body>
<div class="viz-root"><div class="wrap">
<h1>毒駕熱區（鄉鎮層級）</h1>
<p class="sub">三方交集 __N_PEOPLE__ 人，依事件所在鄉鎮市區統計。圓的面積代表人數，三張圖用同一個比例。只有最右邊的版本會對外發布（ε = __EPSILON__，人數低於 __MIN_COUNT__ 的鄉鎮不顯示）。</p>
<div class="maps" id="maps"></div>
<div class="key" id="key"></div>
<div class="scroll"><table id="table"></table></div>
<p class="note">灰點是全部 __N_DISTRICTS__ 個鄉鎮市區的位置。金門縣與連江縣不在地圖範圍內，數字見表格。發布版的人數與平均嚴重度都加了 Laplace 雜訊，所以會與前兩欄不同；人數少的鄉鎮平均嚴重度誤差很大，僅供參考。</p>
</div><div class="tip" id="tip" hidden></div></div>
<script>
const DATA = __DATA__;
const PANELS = [
  {key: "n_real", title: "真實交集", note: "協調者合併後的分析表，不對外"},
  {key: "n_synthetic", title: "PETsARD 合成資料", note: "分析時使用，不含真實個人"},
  {key: "n_published", title: "差分隱私發布版", note: "真實交集加 Laplace 雜訊，對外發布"},
];
const LON = __LON__, LAT = __LAT__, W = 300, PAD = 12;
const kx = Math.cos(23.6 * Math.PI / 180);
const scale = (W - 2 * PAD) / ((LON[1] - LON[0]) * kx);
const H = Math.round((LAT[1] - LAT[0]) * scale + 2 * PAD);
const px = d => PAD + (d.lon - LON[0]) * kx * scale;
const py = d => PAD + (LAT[1] - d.lat) * scale;
const inFrame = d => d.lon >= LON[0] && d.lon <= LON[1] && d.lat >= LAT[0] && d.lat <= LAT[1];
const maxN = Math.max(...DATA.flatMap(d => PANELS.map(p => d[p.key] || 0)));
const radius = n => 2.5 + 13 * Math.sqrt(n / maxN);
const NS = "http://www.w3.org/2000/svg";
const el = (name, attrs, text) => { const e = document.createElementNS(NS, name); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text) e.textContent = text; return e; };
const tip = document.getElementById("tip");
const show = (ev, d) => {
  tip.innerHTML = `<b>${d.county}${d.district}</b>真實 ${d.n_real} 人 · 合成 ${d.n_synthetic} 人 · 發布 ${d.n_published == null ? "不顯示" : d.n_published + " 人"}`;
  tip.hidden = false;
  tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8) + "px";
  tip.style.top = (ev.clientY + 14) + "px";
  document.querySelectorAll(`[data-id="${d.id}"]`).forEach(c => c.classList.add("on"));
};
const hide = () => { tip.hidden = true; document.querySelectorAll(".bubble.on").forEach(c => c.classList.remove("on")); };

for (const panel of PANELS) {
  const box = document.createElement("div"); box.className = "panel";
  box.innerHTML = `<h2>${panel.title}</h2><p>${panel.note}</p>`;
  const svg = el("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${panel.title}：各鄉鎮人數的圓點地圖`});
  const shown = DATA.filter(d => inFrame(d));
  for (const d of shown) svg.append(el("circle", {class: "ground", cx: px(d), cy: py(d), r: 1.2}));
  const marks = shown.filter(d => d[panel.key] > 0).sort((a, b) => b[panel.key] - a[panel.key]);
  for (const d of marks) {
    const c = el("circle", {class: "bubble", "data-id": d.id, cx: px(d), cy: py(d), r: radius(d[panel.key]), tabindex: 0});
    c.addEventListener("pointermove", ev => show(ev, d)); c.addEventListener("pointerleave", hide);
    c.addEventListener("focus", () => { const b = c.getBoundingClientRect(); show({clientX: b.right, clientY: b.top}, d); }); c.addEventListener("blur", hide);
    svg.append(c);
  }
  // one label per panel: the top districts sit next to each other and their labels would collide
  for (const d of marks.slice(0, 1)) svg.append(el("text", {class: "lab", x: px(d) + radius(d[panel.key]) + 3, y: py(d) + 4}, `${d.district} ${d[panel.key]}`));
  box.append(svg); document.getElementById("maps").append(box);
}

const key = document.getElementById("key");
const steps = [1, Math.round(maxN / 4), maxN].filter((v, i, a) => v > 0 && a.indexOf(v) === i);
key.append("圓的面積 = 人數");
for (const n of steps) {
  const r = radius(n), svg = el("svg", {viewBox: `0 0 ${2 * r + 4} 40`, width: 2 * r + 4});
  svg.append(el("circle", {class: "bubble", cx: r + 2, cy: 20, r}));
  const span = document.createElement("span"); span.style.display = "inline-flex"; span.style.alignItems = "center"; span.style.gap = "4px";
  span.append(svg, `${n} 人`); key.append(span);
}

const fmt = v => v == null ? "不顯示" : v;
const rows = [...DATA].sort((a, b) => b.n_real - a.n_real).slice(0, __TOP_ROWS__);
document.getElementById("table").innerHTML = `<caption>真實人數最多的 ${rows.length} 個鄉鎮</caption>
<thead><tr><th>鄉鎮市區</th><th>真實人數</th><th>合成資料人數</th><th>發布人數</th><th>發布的平均嚴重度</th></tr></thead><tbody>` +
  rows.map(d => `<tr><td>${d.county}${d.district}</td><td>${d.n_real}</td><td>${d.n_synthetic}</td><td>${fmt(d.n_published)}</td><td>${fmt(d.severity_mean_published)}</td></tr>`).join("") + "</tbody>";
</script>
</body>
</html>
"""


def main():
    csv_path = Path(sys.argv[1])
    stats = pd.read_csv(csv_path)
    epsilon = csv_path.stem.replace("hotspot_eps", "")

    stats.insert(0, "id", range(len(stats)))
    records = json.loads(stats.to_json(orient="records", force_ascii=False))
    page = PAGE
    for token, value in {
        "__DATA__": json.dumps(records, ensure_ascii=False),
        "__LON__": json.dumps(LON_RANGE), "__LAT__": json.dumps(LAT_RANGE),
        "__N_PEOPLE__": str(int(stats["n_real"].sum())), "__EPSILON__": epsilon,
        "__MIN_COUNT__": str(MIN_PUBLISHED_COUNT), "__N_DISTRICTS__": str(len(stats)), "__TOP_ROWS__": str(TOP_ROWS),
    }.items():
        page = page.replace(token, value)
    output = csv_path.parent / "hotspot_map.html"
    output.write_text(page, encoding="utf-8")
    print(f"{output.name}: {len(stats)} districts, {int(stats['n_published'].notna().sum())} shown in the published map")


if __name__ == "__main__":
    main()
