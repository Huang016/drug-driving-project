// 毒駕熱區地圖：全台（縣市）→ 縣市（鄉鎮）→ 鄉鎮（500m 網格 + 街景）
// 資料由 hotspot_pipeline/build_web_data.py 產生，皆為差分隱私發布後的數字。

const RAMP = ["#fde0d2", "#f9b293", "#f07f56", "#d9512e", "#a8321b"]; // one hue, light -> dark
const WITHHELD = "#d9d8d3";
const CELL_BREAKS = [3, 4, 5, 7, 10]; // noisy cases per 500m cell
const TAIWAN = { north: 25.35, south: 21.85, west: 119.95, east: 122.05 };

const $ = (id) => document.getElementById(id);
const state = { county: null, town: null };
let map, layers, regions, cells, meta, geo, townBreaks, countyBreaks, sv, svService;

// ---------- boot ----------

(async function boot() {
  const key = window.APP_CONFIG && window.APP_CONFIG.googleMapsApiKey;
  if (window.APP_CONFIG_MISSING || !key) return showSetup();
  window.gm_authFailure = () => showNotice("Google Maps 金鑰驗證失敗",
    "請確認 <code>config.js</code> 的金鑰正確、已啟用 Maps JavaScript API，且 HTTP referrer 限制包含目前網址。");
  loadGoogleMaps(key);

  const [regionsJson, cellsJson, topo] = await Promise.all(
    ["data/regions.json", "data/cells.json", "data/towns.topo.json"].map((u) => fetch(u).then((r) => r.json())));
  regions = regionsJson.counties;
  meta = regionsJson.meta;
  cells = cellsJson;
  geo = {
    counties: topojson.feature(topo, topo.objects.counties),
    towns: topojson.feature(topo, topo.objects.towns),
  };
  countyBreaks = breaks(Object.values(regions).map((c) => c.per10k));
  townBreaks = breaks(Object.values(regions).flatMap((c) => Object.values(c.towns).map((t) => t.per10k)));

  const { Map, Data } = await google.maps.importLibrary("maps");
  const { StreetViewPanorama, StreetViewService } = await google.maps.importLibrary("streetView");
  map = new Map($("map"), {
    center: { lat: 23.7, lng: 120.95 }, zoom: 7, minZoom: 6,
    mapTypeControl: false, streetViewControl: false, fullscreenControl: false,
    clickableIcons: false, gestureHandling: "greedy",
    styles: [
      { featureType: "poi", stylers: [{ visibility: "off" }] },
      { featureType: "transit", stylers: [{ visibility: "off" }] },
      { elementType: "geometry", stylers: [{ saturation: -80 }] },
    ],
  });
  layers = { counties: new Data({ map }), towns: new Data({ map }), cells: new Data({ map }) };
  layers.counties.addGeoJson(geo.counties);
  sv = new StreetViewPanorama($("pano"), { visible: false, addressControl: false, fullscreenControl: true, motionTracking: false });
  svService = new StreetViewService();

  bindLayer(layers.counties, (f) => f.getProperty("COUNTYNAME"), (f) => tipCounty(f.getProperty("COUNTYNAME")),
    (f) => go(f.getProperty("COUNTYNAME")));
  bindLayer(layers.towns, (f) => f.getProperty("TOWNNAME"), (f) => tipTown(state.county, f.getProperty("TOWNNAME")),
    (f) => go(state.county, f.getProperty("TOWNNAME")));
  bindLayer(layers.cells, (f) => f.getProperty("id"), (f) => tipCell(f.getProperty("n")),
    (f) => openStreetView(f.getProperty("cell")));
  $("sv-close").onclick = closeStreetView;
  renderPrivacy();
  window.addEventListener("popstate", () => fromHash(false));
  fromHash(false);
})().catch((err) => {
  console.error(err);
  showNotice("載入失敗", `請用本機伺服器開啟（例如 <code>python -m http.server 8000 -d web</code>），不要直接雙擊 HTML。<br><small>${err.message}</small>`);
})

function loadGoogleMaps(key) {
  // Google 官方的 dynamic library import 載入器
  (g=>{var h,a,k,p="The Google Maps JavaScript API",c="google",l="importLibrary",q="__ib__",m=document,b=window;b=b[c]||(b[c]={});var d=b.maps||(b.maps={}),r=new Set,e=new URLSearchParams,u=()=>h||(h=new Promise(async(f,n)=>{await (a=m.createElement("script"));e.set("libraries",[...r]+"");for(k in g)e.set(k.replace(/[A-Z]/g,t=>"_"+t[0].toLowerCase()),g[k]);e.set("callback",c+".maps."+q);a.src=`https://maps.${c}apis.com/maps/api/js?`+e;d[q]=f;a.onerror=()=>h=n(Error(p+" could not load."));a.nonce=m.querySelector("script[nonce]")?.nonce||"";m.head.append(a)}));d[l]?console.warn(p+" only loads once. Ignoring:",g):d[l]=(f,...n)=>r.add(f)&&u().then(()=>d[l](f,...n))})({ key, v: "weekly", language: "zh-TW", region: "TW" });
}

// ---------- navigation ----------

function go(county = null, town = null) {
  state.county = county;
  state.town = town;
  const hash = [county, town].filter(Boolean).map(encodeURIComponent).join("/");
  if (location.hash.slice(1) !== hash) history.pushState(null, "", hash ? `#${hash}` : location.pathname);
  render();
}

function fromHash() {
  const [county = null, town = null] = location.hash.slice(1).split("/").filter(Boolean).map(decodeURIComponent);
  state.county = regions[county] ? county : null;
  state.town = state.county && regions[county].towns[town] ? town : null;
  render();
}

function render() {
  closeStreetView();
  hideTip();
  const { county, town } = state;

  layers.towns.forEach((f) => layers.towns.remove(f));
  layers.cells.forEach((f) => layers.cells.remove(f));
  if (county) layers.towns.addGeoJson({ type: "FeatureCollection", features: geo.towns.features.filter((f) => f.properties.COUNTYNAME === county) });
  if (town) layers.cells.addGeoJson(cellFeatures(county, town));

  layers.counties.setStyle((f) => {
    const name = f.getProperty("COUNTYNAME");
    if (!county) return polyStyle(colorFor(regions[name]?.per10k, countyBreaks), 0.85, true);
    return { fillColor: "#ffffff", fillOpacity: name === county ? 0 : 0.55, strokeColor: "#7a7974", strokeWeight: name === county ? 2 : 0.6, clickable: name !== county, zIndex: 0 };
  });
  layers.towns.setStyle((f) => {
    const name = f.getProperty("TOWNNAME");
    const t = regions[county]?.towns[name];
    if (!town) return polyStyle(colorFor(t?.per10k, townBreaks), 0.85, true);
    return { fillColor: "#ffffff", fillOpacity: name === town ? 0 : 0.5, strokeColor: "#52514e", strokeWeight: name === town ? 2.5 : 0.6, clickable: name !== town, zIndex: 1 };
  });
  layers.cells.setStyle((f) => ({ ...polyStyle(colorFor(f.getProperty("n"), CELL_BREAKS), 0.8, true), strokeColor: "#ffffff", strokeWeight: 1, zIndex: 2 }));

  fit(town ? findFeature(layers.towns, "TOWNNAME", town) : county ? findFeature(layers.counties, "COUNTYNAME", county) : null);
  renderCrumbs();
  renderHeadline();
  renderLegend();
  renderList();
}

function polyStyle(fill, opacity, clickable) {
  return { fillColor: fill, fillOpacity: opacity, strokeColor: "#ffffff", strokeWeight: 1, clickable, cursor: "pointer", zIndex: 1 };
}

function findFeature(layer, prop, value) {
  let hit = null;
  layer.forEach((f) => { if (f.getProperty(prop) === value) hit = f; });
  return hit;
}

function fit(feature) {
  if (!feature) return map.fitBounds(TAIWAN, 0);
  const b = new google.maps.LatLngBounds();
  feature.getGeometry().forEachLatLng((ll) => b.extend(ll));
  map.fitBounds(b, 40);
}

function cellFeatures(county, town) {
  const [dLat, dLng] = meta.cellDeg;
  const list = cells[`${county}|${town}`] || [];
  return {
    type: "FeatureCollection",
    features: list.map(([lat, lng, n], i) => ({
      type: "Feature",
      properties: { id: `${town}-${i}`, n, cell: { lat, lng, n, rank: i } },
      geometry: { type: "Polygon", coordinates: [[
        [lng - dLng / 2, lat - dLat / 2], [lng + dLng / 2, lat - dLat / 2],
        [lng + dLng / 2, lat + dLat / 2], [lng - dLng / 2, lat + dLat / 2], [lng - dLng / 2, lat - dLat / 2],
      ]] },
    })),
  };
}

// ---------- color ----------

function breaks(values) {
  // five classes at the quantiles of the published values, rounded to readable numbers
  const v = values.filter((x) => x != null).sort((a, b) => a - b);
  const q = (p) => v[Math.min(v.length - 1, Math.floor(p * v.length))];
  const nice = (x) => (x >= 100 ? Math.round(x / 10) * 10 : Math.round(x));
  return [v[0], q(0.2), q(0.4), q(0.6), q(0.8)].map(nice);
}

function colorFor(value, br) {
  if (value == null) return WITHHELD;
  let i = 0;
  while (i < br.length - 1 && value >= br[i + 1]) i++;
  return RAMP[i];
}

// ---------- panel ----------

function renderCrumbs() {
  const el = $("crumbs");
  el.innerHTML = "";
  const parts = [["全台", () => go()]];
  if (state.county) parts.push([state.county, () => go(state.county)]);
  if (state.town) parts.push([state.town, null]);
  parts.forEach(([label, fn], i) => {
    if (i) el.append(Object.assign(document.createElement("span"), { className: "sep", textContent: "›" }));
    const last = i === parts.length - 1;
    const node = document.createElement(last ? "span" : "button");
    node.textContent = label;
    if (last) node.className = "here";
    else { node.type = "button"; node.onclick = fn; }
    el.append(node);
  });
}

function fmt(n, digits = 0) {
  return n == null ? "—" : Number(n).toLocaleString("zh-TW", { maximumFractionDigits: digits });
}

function renderHeadline() {
  const { county, town } = state;
  const el = $("headline");
  let rec, stats;
  if (town) {
    rec = regions[county].towns[town];
    const list = cells[`${county}|${town}`] || [];
    stats = [
      [fmt(rec.per10k, 1), "每萬件事故毒駕數"],
      [fmt(rec.drug), "毒駕事故（加噪）", true],
      [rec.severity == null ? "—" : fmt(rec.severity, 2), "平均毒品嚴重度（1–3）", true],
      [rec.recidivistShare == null ? "—" : `${Math.round(rec.recidivistShare * 100)}%`, "再犯比例", true],
      [fmt(list.length), "公開熱點網格", true],
    ];
  } else {
    rec = county ? regions[county] : nationTotals();
    stats = [
      [fmt(rec.per10k, 1), "每萬件事故毒駕數"],
      [fmt(rec.drug), "毒駕事故（加噪）", true],
      [fmt(rec.total), "傷亡事故總數", true],
    ];
  }
  el.innerHTML = stats.map(([v, k, small]) => `<div class="stat${small ? " small" : ""}"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("");
  if (rec.drug == null) el.insertAdjacentHTML("beforeend", `<p class="sub">加噪後少於 ${meta.minDistrictCount} 件，為避免推回個人，不公開數字。</p>`);
}

function nationTotals() {
  const all = Object.values(regions);
  const total = all.reduce((s, c) => s + c.total, 0);
  const drug = all.reduce((s, c) => s + (c.drug || 0), 0);
  return { total, drug, per10k: (drug / total) * 1e4 };
}

function renderLegend() {
  const town = state.town;
  const br = town ? CELL_BREAKS : state.county ? townBreaks : countyBreaks;
  const title = town ? `500m 網格內毒駕事故數（加噪，≥ ${meta.minCellCount} 才公開）` : "每萬件傷亡事故中的毒駕事故數";
  $("legend").innerHTML = `<h2>${title}</h2>
    <div class="bar">${RAMP.map((c) => `<span style="background:${c}"></span>`).join("")}</div>
    <div class="ticks">${br.map((b) => `<span>${b}</span>`).join("")}<span>${town ? "+" : ""}</span></div>
    ${town ? "" : `<div class="withheld"><i></i>少於 ${meta.minDistrictCount} 件，不公開</div>`}`;
}

function renderList() {
  const { county, town } = state;
  const ol = $("list");
  ol.innerHTML = "";
  let items;
  if (town) {
    $("list-title").textContent = "熱點網格（點選看街景）";
    items = (cells[`${county}|${town}`] || [])
      .map(([lat, lng, n], i) => ({ label: `網格 ${i + 1}`, value: n, color: colorFor(n, CELL_BREAKS), shown: `${n} 件`, act: () => openStreetView({ lat, lng, n, rank: i }) }))
      .sort((a, b) => b.value - a.value)
      .map((x, i) => ({ ...x, label: `熱點 ${i + 1}` }));
    if (!items.length) ol.innerHTML = `<li class="sub">這個鄉鎮沒有加噪後 ≥ ${meta.minCellCount} 件的網格。</li>`;
  } else {
    const source = county ? regions[county].towns : regions;
    const br = county ? townBreaks : countyBreaks;
    $("list-title").textContent = county ? `${county}各鄉鎮市區（點選進入）` : "各縣市（點選進入）";
    items = Object.entries(source)
      .map(([name, r]) => ({ label: name, value: r.per10k, color: colorFor(r.per10k, br), shown: r.per10k == null ? "不公開" : fmt(r.per10k, 1),
        act: () => (county ? go(county, name) : go(name)) }))
      .sort((a, b) => (b.value ?? -1) - (a.value ?? -1));
  }
  const max = Math.max(...items.map((x) => x.value || 0), 1);
  items.forEach((x, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<button type="button"><span class="rank">${i + 1}</span>
      <span class="name"><span>${x.label}</span><span class="meter"><i style="width:${((x.value || 0) / max) * 100}%;background:${x.color}"></i></span></span>
      <span class="val${x.value == null ? " na" : ""}">${x.shown}</span></button>`;
    li.querySelector("button").onclick = x.act;
    ol.append(li);
  });
}

function renderPrivacy() {
  $("privacy-body").innerHTML = `<ol>
    <li>警政、檢驗、監理三方用 <b>PSI</b> 找出「毒駕且肇事」的交集，資料以後量子加密傳輸，各機構看不到彼此的名單。</li>
    <li>縣市與鄉鎮數字在真實交集上加 <b>Laplace 雜訊</b>（ε = ${meta.epsilonDistrict}），加噪後少於 ${meta.minDistrictCount} 件不公開。</li>
    <li>精確 GPS 一律轉成 <b>${meta.cellMeters}m × ${meta.cellMeters}m 網格</b>，每格再加雜訊（ε = ${meta.epsilonGrid}），只公開加噪後 ≥ ${meta.minCellCount} 件的網格。有事故的網格都會加噪，不只是有毒駕的，所以「網格有沒有出現」本身不會洩漏。</li>
    <li><b>街景</b>顯示的是網格中心附近最近的道路，不是事故的精確地點。</li>
    <li>總隱私預算 ε = ${meta.epsilonTotal}。比例的分母是公開的 114 年傷亡事故件數。</li>
  </ol>
  <p>資料來源：${meta.source}。數字含雜訊，件數少的地區排名僅供參考。</p>`;
}

// ---------- hover ----------

function bindLayer(layer, idOf, tipOf, clickOf) {
  layer.addListener("mouseover", (e) => {
    layer.overrideStyle(e.feature, { strokeWeight: 2.5, strokeColor: "#0b0b0b", zIndex: 3 });
    showTip(e, tipOf(e.feature));
  });
  layer.addListener("mousemove", (e) => moveTip(e));
  layer.addListener("mouseout", (e) => { layer.revertStyle(e.feature); hideTip(); });
  layer.addListener("click", (e) => clickOf(e.feature));
}

function tipCounty(name) {
  const r = regions[name];
  if (!r) return `<b>${name}</b>無資料`;
  return `<b>${name}</b>${r.per10k == null ? "件數過少，不公開" : `每萬件事故 ${fmt(r.per10k, 1)} 件毒駕<br>毒駕 ${fmt(r.drug)} / 事故 ${fmt(r.total)}`}`;
}

function tipTown(county, name) {
  const r = regions[county]?.towns[name];
  if (!r) return `<b>${name}</b>無資料`;
  return `<b>${county}${name}</b>${r.per10k == null ? `件數過少（< ${meta.minDistrictCount}），不公開` : `每萬件事故 ${fmt(r.per10k, 1)} 件毒駕<br>毒駕 ${fmt(r.drug)} / 事故 ${fmt(r.total)}`}`;
}

function tipCell(n) {
  return `<b>${meta.cellMeters}m 網格</b>毒駕事故約 ${n} 件（加噪）<br>點一下看街景`;
}

function showTip(e, html) {
  const tip = $("tip");
  tip.innerHTML = html;
  tip.hidden = false;
  moveTip(e);
}

function moveTip(e) {
  const tip = $("tip");
  if (tip.hidden || !e.domEvent) return;
  const box = $("map").getBoundingClientRect();
  const x = e.domEvent.clientX - box.left, y = e.domEvent.clientY - box.top;
  tip.style.left = `${Math.min(x + 14, box.width - tip.offsetWidth - 8)}px`;
  tip.style.top = `${Math.min(y + 14, box.height - tip.offsetHeight - 8)}px`;
}

function hideTip() {
  $("tip").hidden = true;
}

// ---------- street view ----------

async function openStreetView(cell) {
  const box = $("sv");
  box.hidden = false;
  $("sv-title").textContent = `${state.county}${state.town}・熱點網格`;
  $("sv-sub").textContent = `${meta.cellMeters}m 網格內毒駕事故約 ${cell.n} 件（加噪）`;
  $("sv-foot").textContent = "搜尋最近的街景…";
  sv.setVisible(false);
  map.panTo({ lat: cell.lat, lng: cell.lng });
  if (map.getZoom() < 15) map.setZoom(15);
  try {
    const { data } = await svService.getPanorama({
      location: { lat: cell.lat, lng: cell.lng },
      radius: meta.cellMeters / 2,
      preference: google.maps.StreetViewPreference.NEAREST,
      sources: [google.maps.StreetViewSource.OUTDOOR],
    });
    sv.setPano(data.location.pano);
    sv.setPov({ heading: 0, pitch: 0 });
    sv.setVisible(true);
    const ll = data.location.latLng;
    $("sv-foot").innerHTML = `街景位置是網格中心附近最近的道路，<b>不是事故的精確地點</b>。${data.imageDate ? `拍攝時間 ${data.imageDate}・` : ""}<a href="https://www.google.com/maps/@?api=1&map_action=pano&pano=${encodeURIComponent(data.location.pano)}&viewpoint=${ll.lat()},${ll.lng()}" target="_blank" rel="noopener">在 Google 地圖開啟</a>`;
  } catch {
    $("sv-foot").textContent = `網格中心 ${meta.cellMeters / 2}m 內沒有 Google 街景。`;
  }
}

function closeStreetView() {
  if (!sv) return;
  sv.setVisible(false);
  $("sv").hidden = true;
}

// ---------- notices ----------

function showNotice(title, html) {
  const el = $("notice");
  el.innerHTML = `<div class="card"><h2>${title}</h2><p>${html}</p></div>`;
  el.hidden = false;
}

function showSetup() {
  showNotice("需要 Google Maps API 金鑰", `
    1. 到 Google Cloud Console 建立 API 金鑰，並啟用 <b>Maps JavaScript API</b>。<br>
    2. 把 <code>web/config.example.js</code> 複製成 <code>web/config.js</code>，填入金鑰：
    <pre>window.APP_CONFIG = { googleMapsApiKey: "你的金鑰" };</pre>
    3. 重新整理這個頁面。`);
}
