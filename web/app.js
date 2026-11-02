// 毒駕熱區地圖：全台（縣市）→ 縣市（鄉鎮）→ 鄉鎮（500m 網格 + 街景）
// 資料由 hotspot_pipeline/build_web_data.py 產生。毒駕事故是差分隱私發布後的數字；
// 一般事故是公開的 114 年傷亡事故資料，不加雜訊，熱點細到半徑 30m（約一個路口）。
// 預防建議由 hotspot_pipeline/build_prevention.py 產生：毒駕風險最高的地點，與各地點的降速、科技執法、照明、路檢建議。

const RAMP = ["#fde0d2", "#f9b293", "#f07f56", "#d9512e", "#a8321b"]; // one hue, light -> dark
const WITHHELD = "#d9d8d3";
const CELL_BREAKS = [3, 4, 5, 7, 10]; // noisy cases per 500m cell
const TAIWAN = { north: 25.35, south: 21.85, west: 119.95, east: 122.05 };
const ALL_MODE = "一般事故"; // the first part of the URL hash in the general-accident view
const PREVENT_MODE = "預防建議";
const PREVENT_TOP = 10;
const PREVENT_COLOR = "#6a3fa0";

const $ = (id) => document.getElementById(id);
// the drug-driving map can be coloured by the count (where the cases are) or by the rate per
// 10,000 casualty accidents (where an accident is most likely drug-related)
const METRICS = {
  drug: { label: "毒駕件數", title: "毒駕事故件數（加噪）", digits: 0, unit: " 件" },
  per10k: { label: "每萬件事故比例", title: "每萬件傷亡事故中的毒駕事故數", digits: 1, unit: "" },
};
const state = { mode: "drug", rank: "n", metric: "drug", county: null, town: null };
let map, layers, regions, cells, meta, geo, townBreaks, countyBreaks, sv, svService; // breaks: { drug, per10k }
let general, allCountyBreaks, allTownBreaks, MarkerClass, prevention;
let spotMarkers = [];
const spotNames = new Map(); // "lat,lng" -> road name from the nearest street view, looked up once

// ---------- boot ----------

(async function boot() {
  const key = window.APP_CONFIG && window.APP_CONFIG.googleMapsApiKey;
  if (window.APP_CONFIG_MISSING || !key) return showSetup();
  window.gm_authFailure = () => showNotice("Google Maps 金鑰驗證失敗",
    "請確認 <code>config.js</code> 的金鑰正確、已啟用 Maps JavaScript API，且 HTTP referrer 限制包含目前網址。");
  loadGoogleMaps(key);

  const [regionsJson, cellsJson, topo, generalJson, preventionJson] = await Promise.all(
    ["data/regions.json", "data/cells.json", "data/towns.topo.json", "data/general.json", "data/prevention.json"].map((u) => fetch(u, { cache: "no-cache" }).then((r) => r.json())));
  prevention = preventionJson;
  regions = regionsJson.counties;
  meta = regionsJson.meta;
  cells = cellsJson;
  general = generalJson;
  const allCounties = Object.values(general.profiles.counties);
  allCountyBreaks = breaks(allCounties.map((c) => c.profile.total));
  allTownBreaks = breaks(allCounties.flatMap((c) => Object.values(c.towns).map((t) => t.total)));
  geo = {
    counties: topojson.feature(topo, topo.objects.counties),
    towns: topojson.feature(topo, topo.objects.towns),
  };
  countyBreaks = {}, townBreaks = {};
  for (const k of Object.keys(METRICS)) {
    countyBreaks[k] = breaks(Object.values(regions).map((c) => c[k]));
    townBreaks[k] = breaks(Object.values(regions).flatMap((c) => Object.values(c.towns).map((t) => t[k])));
  }

  const { Map, Data } = await google.maps.importLibrary("maps");
  const { StreetViewPanorama, StreetViewService } = await google.maps.importLibrary("streetView");
  ({ Marker: MarkerClass } = await google.maps.importLibrary("marker"));
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
  document.querySelectorAll(".modes button").forEach((b) => {
    b.onclick = () => { state.mode = b.dataset.mode; go(state.county, state.town); };
  });
  $("rank-select").onchange = (e) => {
    if (state.mode === "drug") state.metric = e.target.value;
    else state.rank = e.target.value;
    render();
  };
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
  const hash = [{ all: ALL_MODE, prevent: PREVENT_MODE }[state.mode] || null, county, town].filter(Boolean).map(encodeURIComponent).join("/");
  if (location.hash.slice(1) !== hash) history.pushState(null, "", hash ? `#${hash}` : location.pathname);
  render();
}

function fromHash() {
  const parts = location.hash.slice(1).split("/").filter(Boolean).map(decodeURIComponent);
  state.mode = parts[0] === ALL_MODE ? "all" : parts[0] === PREVENT_MODE ? "prevent" : "drug";
  const [county = null, town = null] = state.mode === "drug" ? parts : parts.slice(1);
  state.county = regions[county] ? county : null;
  state.town = state.county && regions[county].towns[town] ? town : null;
  render();
}

function profileOf(county, town) {
  if (!county) return general.profiles.nation;
  const c = general.profiles.counties[county];
  return town ? c?.towns[town] : c?.profile;
}

function render() {
  closeStreetView();
  hideTip();
  const { county, town, mode } = state;
  const all = mode === "all";
  const prev = mode === "prevent";

  document.querySelectorAll(".modes button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  const drug = mode === "drug";
  $("rank-by").hidden = !(all || (drug && !town));
  $("rank-label").textContent = drug ? "地圖顏色" : "熱點排名依據";
  const options = drug ? Object.fromEntries(Object.entries(METRICS).map(([k, v]) => [k, v.label])) : general.meta.rankings;
  $("rank-select").innerHTML = Object.entries(options).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  $("rank-select").value = drug ? state.metric : state.rank;
  $("subtitle").textContent = prev ? "毒駕風險最高的地點，與降低傷亡、科技執法的建議（供實地評估參考）"
    : all ? "114 年傷亡道路交通事故與路口事故熱點（公開資料）" : `${METRICS[state.metric].title.replace("（加噪）", "")}（差分隱私發布）`;

  layers.towns.forEach((f) => layers.towns.remove(f));
  layers.cells.forEach((f) => layers.cells.remove(f));
  if (county) layers.towns.addGeoJson({ type: "FeatureCollection", features: geo.towns.features.filter((f) => f.properties.COUNTYNAME === county) });
  if (town && mode === "drug") layers.cells.addGeoJson(cellFeatures(county, town));

  const countyValue = (name) => (all ? general.profiles.counties[name]?.profile.total : regions[name]?.[state.metric]);
  const townValue = (name) => (all ? general.profiles.counties[county]?.towns[name]?.total : regions[county]?.towns[name]?.[state.metric]);
  layers.counties.setStyle((f) => {
    const name = f.getProperty("COUNTYNAME");
    if (prev) return { fillColor: "#ffffff", fillOpacity: name === county ? 0 : 0.35, strokeColor: "#7a7974", strokeWeight: name === county ? 2 : 0.8, clickable: name !== county, cursor: "pointer", zIndex: 0 };
    if (!county) return polyStyle(colorFor(countyValue(name), all ? allCountyBreaks : countyBreaks[state.metric]), 0.85, true);
    return { fillColor: "#ffffff", fillOpacity: name === county ? 0 : 0.55, strokeColor: "#7a7974", strokeWeight: name === county ? 2 : 0.6, clickable: name !== county, zIndex: 0 };
  });
  layers.towns.setStyle((f) => {
    const name = f.getProperty("TOWNNAME");
    if (prev) return { fillColor: "#ffffff", fillOpacity: name === town ? 0 : 0.3, strokeColor: "#7a7974", strokeWeight: name === town ? 2.5 : 0.6, clickable: name !== town, cursor: "pointer", zIndex: 1 };
    if (!town) return polyStyle(colorFor(townValue(name), all ? allTownBreaks : townBreaks[state.metric]), all ? 0.55 : 0.85, true);
    return { fillColor: "#ffffff", fillOpacity: name === town ? 0 : 0.5, strokeColor: "#52514e", strokeWeight: name === town ? 2.5 : 0.6, clickable: name !== town, zIndex: 1 };
  });
  layers.cells.setStyle((f) => ({ ...polyStyle(colorFor(f.getProperty("n"), CELL_BREAKS), 0.8, true), strokeColor: "#ffffff", strokeWeight: 1, zIndex: 2 }));
  drawSpots(all && county ? currentSpots() : []);
  const area = town ? findFeature(layers.towns, "TOWNNAME", town) : county ? findFeature(layers.counties, "COUNTYNAME", county) : null;
  drawPrevent(prev ? preventItems() : [], prev && county ? area : null);

  fit(area);
  renderCrumbs();
  renderHeadline();
  renderLegend();
  renderSpotList();
  renderList();
  renderPrivacy();
}

// ---------- accident spots (一般事故) ----------

function currentSpots() {
  const { county, town, rank } = state;
  const area = town ? general.spots.town[`${county}|${town}`] : general.spots.county[county];
  return (area?.[rank] || []).map(([lat, lng, n, deaths, injuries, stacked], i) => ({ lat, lng, n, deaths, injuries, stacked: !!stacked, rank: i + 1 }));
}

function drawSpots(spots) {
  spotMarkers.forEach((m) => m.setMap(null));
  spotMarkers = spots.map((s) => {
    const m = new MarkerClass({
      map, position: { lat: s.lat, lng: s.lng }, zIndex: 100 - s.rank, title: `熱點 ${s.rank}`,
      icon: { path: google.maps.SymbolPath.CIRCLE, scale: 15, fillColor: "#f5d33f", fillOpacity: 0.85, strokeColor: "#8a6d00", strokeWeight: 1.5 },
      label: { text: String(s.rank), color: "#a8321b", fontSize: "14px", fontWeight: "700" },
    });
    m.addListener("click", () => openSpot(s));
    m.addListener("mouseover", (e) => showTip(e, spotTip(s)));
    m.addListener("mouseout", hideTip);
    return m;
  });
}

function spotKey(s) {
  return `${s.lat},${s.lng}`;
}

function spotValue(s) {
  return { n: s.n, deaths: s.deaths, injuries: s.injuries }[state.rank];
}

function spotTip(s) {
  return `<b>熱點 ${s.rank}${spotNames.get(spotKey(s)) ? `・${spotNames.get(spotKey(s))}` : ""}</b>傷亡事故 ${fmt(s.n)} 件・死亡 ${fmt(s.deaths)}・受傷 ${fmt(s.injuries)}<br>點一下看街景`;
}

function panoSources() {
  // Google's own imagery only: user-uploaded photo spheres carry the uploader's name as their description
  return [google.maps.StreetViewSource.GOOGLE || google.maps.StreetViewSource.OUTDOOR];
}

function cleanRoad(text) {
  // "4 長安西路, 臺北, 臺北市" -> "長安西路"; a bare city name is no road name
  const first = (text || "").split(",")[0].replace(/^[\s\d\-之號]+/, "").trim();
  return /[路街道橋巷段弄口線]/.test(first) ? first : null;
}

let geocoder, geocoderOff = false;
async function geocodedRoad(s) {
  // reverse geocoding names a junction (「長安西路與重慶北路口」-like) when the key has the Geocoding API enabled
  if (geocoderOff) return null;
  try {
    if (!geocoder) geocoder = new (await google.maps.importLibrary("geocoding")).Geocoder();
    const { results } = await geocoder.geocode({ location: { lat: s.lat, lng: s.lng }, language: "zh-TW" });
    const junction = results.find((r) => r.types.includes("intersection"));
    if (junction) return cleanRoad(junction.formatted_address) || junction.formatted_address.split(",")[0];
    const route = results.flatMap((r) => r.address_components).find((c) => c.types.includes("route"));
    return route ? route.long_name : null;
  } catch {
    geocoderOff = true; // not enabled for this key: use the street view names from here on
    return null;
  }
}

async function roadName(s) {
  const key = spotKey(s);
  if (spotNames.has(key)) return spotNames.get(key);
  let name = await geocodedRoad(s);
  if (!name) {
    try {
      const { data } = await svService.getPanorama({ location: { lat: s.lat, lng: s.lng }, radius: 60,
        preference: google.maps.StreetViewPreference.NEAREST, sources: panoSources() });
      name = cleanRoad(data.location.description);
    } catch { /* no street view within 60m */ }
  }
  spotNames.set(key, name);
  return name;
}

function renderSpotList() {
  const { county, town, mode } = state;
  const wrap = $("spots-wrap");
  wrap.hidden = !((mode === "all" && county) || mode === "prevent");
  if (wrap.hidden) return;
  if (mode === "prevent") return renderPreventList();
  const spots = currentSpots();
  const label = general.meta.rankings[state.rank];
  $("spots-title").textContent = `${town || county}事故熱點前 ${general.meta.spotsPerArea} 名（依${label}，點選看街景）`;
  const ol = $("spots");
  ol.innerHTML = spots.length ? "" : `<li class="sub">沒有符合的熱點。</li>`;
  const max = Math.max(...spots.map(spotValue), 1);
  spots.forEach((s) => {
    const li = document.createElement("li");
    li.innerHTML = `<button type="button"><span class="rank">${s.rank}</span>
      <span class="name"><span class="road">${spotNames.get(spotKey(s)) || "查詢路名…"}</span>
      ${s.stacked ? `<span class="warn">多數事故座標相同，可能是登記地址而非事故地點</span>` : ""}
      <span class="meter"><i style="width:${(spotValue(s) / max) * 100}%;background:${RAMP[3]}"></i></span></span>
      <span class="val">${fmt(spotValue(s))} ${state.rank === "n" ? "件" : "人"}</span></button>`;
    li.querySelector("button").onclick = () => openSpot(s);
    ol.append(li);
    roadName(s).then((name) => { li.querySelector(".road").textContent = name || `（未取得路名）${s.lat.toFixed(4)}, ${s.lng.toFixed(4)}`; });
  });
}

async function openSpot(s) {
  const box = $("sv");
  box.hidden = false;
  $("sv-title").textContent = `熱點 ${s.rank}・${state.town ? state.county + state.town : state.county}`;
  $("sv-sub").textContent = `傷亡事故 ${fmt(s.n)} 件・死亡 ${fmt(s.deaths)} 人・受傷 ${fmt(s.injuries)} 人（114 年）`;
  $("sv-foot").textContent = "搜尋最近的街景…";
  sv.setVisible(false);
  map.panTo({ lat: s.lat, lng: s.lng });
  if (map.getZoom() < 17) map.setZoom(17);
  try {
    const { data } = await svService.getPanorama({ location: { lat: s.lat, lng: s.lng }, radius: 60,
      preference: google.maps.StreetViewPreference.NEAREST, sources: panoSources() });
    sv.setPano(data.location.pano);
    sv.setPov({ heading: 0, pitch: 0 });
    sv.setVisible(true);
    const ll = data.location.latLng;
    const road = (await roadName(s)) || cleanRoad(data.location.description);
    $("sv-foot").innerHTML = `${road ? `<b>${road}</b>・` : ""}熱點是 半徑 ${general.meta.spotRadius}m 內事故的中心位置。${s.stacked ? "多數事故座標相同，可能是登記地址而非事故地點。" : ""}${data.imageDate ? `拍攝時間 ${data.imageDate}・` : ""}<a href="https://www.google.com/maps/@?api=1&map_action=pano&pano=${encodeURIComponent(data.location.pano)}&viewpoint=${ll.lat()},${ll.lng()}" target="_blank" rel="noopener">在 Google 地圖開啟</a>`;
  } catch {
    $("sv-foot").textContent = "熱點 60m 內沒有 Google 街景。";
  }
}

// ---------- prevention (預防建議) ----------

let preventMarkers = [];
const TIERS = {
  1: { label: "第一優先", short: "立即改善", color: "#c0321e" },
  2: { label: "第二優先", short: "需要改善", color: "#e07b1a" },
  3: { label: "第三優先", short: "加強路檢", color: "#3f7cb0" },
};

function range95(n) {
  const m = prevention.meta.margin95;
  return `${fmt(Math.max(0, n - m), 1)}–${fmt(n + m, 1)}`;
}

function measureList(s) {
  // [short tag, full sentence] for each measure the cell's public numbers point to
  const m = prevention.meta;
  const pct = (x) => `${Math.round(x * 100)}%`;
  const text = {
    camera: ["測速照相", "格內沒有固定測速照相：建議設置測速照相或區間測速，並評估降低速限。"],
    junction: ["路口科技執法", `路口事故集中（最多的路口 ${s.junction?.[2]} 件）：建議闖紅燈科技執法、號誌時制與路口設計改善。`],
    night: ["夜間照明與監測", `夜間（${m.nightHours[0]}–${m.nightHours[1]} 時）事故占 ${pct(s.night)}，高於全台 ${pct(m.nightAvg)}：建議改善照明、設置異常行車監測。`],
    fatal: ["優先降速", `曾有死亡 ${s.deaths} 人：建議優先降速與增設警示。`],
    patrol: ["路檢時段", `建議路檢時段：${s.peakHours.map((h) => `${h}–${h + 1} 時`).join("、") || "—"}。`],
  };
  return s.measures.map((k) => text[k]);
}

function preventItems() {
  const { county, town } = state;
  if (!county) return countyAreas();
  return prevention.cells.filter((r) => r.county === county && (!town || r.district === town))
    .slice(0, PREVENT_TOP).map((r, i) => ({ ...r, rank: i + 1 }));
}

function countyAreas() {
  // nationwide: counties by the published (noisy) drug-driving counts; the marker sits on
  // the county's riskiest published cell
  return Object.entries(regions)
    .filter(([, r]) => r.drug != null)
    .map(([name, r]) => {
      const cells = prevention.cells.filter((p) => p.county === name);
      return { area: true, county: name, name, n: Math.round(r.drug), lat: cells[0]?.lat, lng: cells[0]?.lng,
        places: cells.length, tier1: cells.filter((c) => c.tier === 1).length };
    })
    .sort((a, b) => b.n - a.n)
    .slice(0, PREVENT_TOP)
    .map((r, i) => ({ ...r, rank: i + 1 }));
}

function preventClick(s) {
  return s.area ? go(s.county) : openPrevent(s);
}

function drawPrevent(items, area) {
  preventMarkers.forEach((m) => m.setMap(null));
  preventMarkers = items.filter((s) => s.lat != null).map((s) => {
    const m = new MarkerClass({
      map, position: { lat: s.lat, lng: s.lng }, zIndex: 200 - s.rank, title: `毒駕高風險地點 ${s.rank}`,
      icon: { path: google.maps.SymbolPath.CIRCLE, scale: 14, fillColor: s.area ? PREVENT_COLOR : TIERS[s.tier].color, fillOpacity: 0.92, strokeColor: "#ffffff", strokeWeight: 2 },
      label: { text: String(s.rank), color: "#ffffff", fontSize: "13px", fontWeight: "700" },
    });
    m.addListener("click", () => preventClick(s));
    m.addListener("mouseover", (e) => showTip(e, preventTip(s)));
    m.addListener("mouseout", hideTip);
    return m;
  });
  if (!area) return;
  // the fixed speed cameras already there, so the gaps show
  const b = new google.maps.LatLngBounds();
  area.getGeometry().forEachLatLng((ll) => b.extend(ll));
  prevention.cameras.filter(([lat, lng]) => b.contains({ lat, lng })).forEach(([lat, lng]) => {
    preventMarkers.push(new MarkerClass({
      map, position: { lat, lng }, zIndex: 1, clickable: false,
      icon: { path: google.maps.SymbolPath.CIRCLE, scale: 4, fillColor: "#7a7974", fillOpacity: 0.9, strokeColor: "#ffffff", strokeWeight: 1 },
    }));
  });
}

function preventTitle(s) {
  if (s.area) return s.name;
  return spotNames.get(spotKey(s)) || `${s.county}${s.district}`;
}

function preventDetail(s) {
  if (s.area) return `毒駕事故約 ${fmt(s.n)} 件（加噪）・熱點 ${s.places} 處，第一優先 ${s.tier1} 處`;
  return `${TIERS[s.tier].label}・毒駕事故約 ${s.n} 件（95% 落在 ${range95(s.n)}）・500m 內傷亡事故 ${fmt(s.accidents)} 件${s.deaths ? `・死亡 ${s.deaths} 人` : ""}・固定測速照相 ${s.cameras} 支`;
}

function preventTip(s) {
  if (s.area) return `<b>${s.rank}. ${s.name}</b>${preventDetail(s)}<br>點一下進入`;
  return `<b>${s.rank}. ${preventTitle(s)}</b>${preventDetail(s)}<br>建議：${measureList(s).map(([t]) => t).join("、")}<br>點一下看街景`;
}

function renderPreventList() {
  const { county, town } = state;
  const items = preventItems();
  $("spots-title").textContent = county
    ? `${town ? county + town : county}毒駕風險最高的 ${PREVENT_TOP} 個地點（點選看建議與街景）`
    : `各縣市毒駕事故前 ${PREVENT_TOP} 名（點選進入，看具體地點）`;
  const ol = $("spots");
  ol.innerHTML = items.length ? "" : `<li class="sub">這個地區沒有公開的毒駕熱點網格。</li>`;
  const max = Math.max(...items.map((s) => s.n), 1);
  items.forEach((s) => {
    const li = document.createElement("li");
    const tags = s.area ? "" : `<span class="tags"><b style="background:${TIERS[s.tier].color}">${TIERS[s.tier].label}</b>${measureList(s).filter(([t]) => t !== "路檢時段").map(([t]) => `<i>${t}</i>`).join("")}</span>`;
    li.innerHTML = `<button type="button"><span class="rank">${s.rank}</span>
      <span class="name"><span class="road">${s.area ? s.name : "查詢路名…"}</span>
      <span class="detail">${s.area ? `熱點 ${s.places} 處・第一優先 ${s.tier1} 處` : `${town ? "" : `${s.district}・`}傷亡事故 ${fmt(s.accidents)} 件・路檢 ${s.peakHours.map((h) => `${h}時`).join("、")}`}</span>
      ${tags}
      <span class="meter"><i style="width:${(s.n / max) * 100}%;background:${s.area ? PREVENT_COLOR : TIERS[s.tier].color}"></i></span></span>
      <span class="val">約 ${fmt(s.n)} 件${s.area ? "" : `<small>±${prevention.meta.margin95}</small>`}</span></button>`;
    li.querySelector("button").onclick = () => preventClick(s);
    ol.append(li);
    if (!s.area) {
      roadName(s).then((name) => { li.querySelector(".road").textContent = name || `${s.county}${s.district}（${s.lat.toFixed(4)}, ${s.lng.toFixed(4)}）`; });
    }
  });
}

async function openPrevent(s) {
  const box = $("sv");
  box.hidden = false;
  // the busiest junction is where a camera or a redesign would go; otherwise the cell centre
  const at = s.junction && s.measures.includes("junction") ? { lat: s.junction[0], lng: s.junction[1] } : { lat: s.lat, lng: s.lng };
  $("sv-title").textContent = `${TIERS[s.tier].label} ${s.rank}・${preventTitle(s)}`;
  $("sv-sub").innerHTML = `${preventDetail(s)}<ul class="measures">${measureList(s).map(([, t]) => `<li>${t}</li>`).join("")}</ul>`;
  $("sv-foot").textContent = "搜尋最近的街景…";
  sv.setVisible(false);
  map.panTo(at);
  if (map.getZoom() < 16) map.setZoom(16);
  const note = at.lat === s.lat ? "街景位於 500m 網格中心附近，<b>不是毒駕事故的精確地點</b>。" : "街景位於格內事故最多的路口（公開事故資料）。";
  try {
    const { data } = await svService.getPanorama({ location: at, radius: 120,
      preference: google.maps.StreetViewPreference.NEAREST, sources: panoSources() });
    sv.setPano(data.location.pano);
    sv.setPov({ heading: 0, pitch: 0 });
    sv.setVisible(true);
    const ll = data.location.latLng;
    $("sv-foot").innerHTML = `${note}${data.imageDate ? `拍攝時間 ${data.imageDate}・` : ""}<a href="https://www.google.com/maps/@?api=1&map_action=pano&pano=${encodeURIComponent(data.location.pano)}&viewpoint=${ll.lat()},${ll.lng()}" target="_blank" rel="noopener">在 Google 地圖開啟</a>`;
  } catch {
    $("sv-foot").innerHTML = `${note}附近沒有 Google 街景。`;
  }
}

function renderPreventHeadline() {
  const { county, town } = state;
  const rec = town ? regions[county].towns[town] : county ? regions[county] : nationTotals();
  const cellsIn = prevention.cells.filter((r) => (!county || r.county === county) && (!town || r.district === town));
  const count = (k) => cellsIn.filter((c) => c.measures.includes(k)).length;
  const tier = (t) => cellsIn.filter((c) => c.tier === t).length;
  const stats = [[rec.drug == null ? "不公開" : fmt(rec.drug), "毒駕事故（加噪）"], [fmt(cellsIn.length), "毒駕熱點（500m）", true],
    [fmt(tier(1)), "第一優先：立即改善", true]];
  const where = town ? county + town : county || "全台";
  const first = cellsIn[0];
  const summary = cellsIn.length
    ? `<b>${where}</b>的 ${fmt(cellsIn.length)} 個毒駕熱點中，${fmt(count("camera"))} 處沒有固定測速照相、${fmt(count("junction"))} 處路口事故集中、${fmt(count("night"))} 處夜間事故偏高、${fmt(count("fatal"))} 處發生過死亡事故。`
    + (county && first ? `毒駕風險最高的是 <b>${first.district}</b> 的第 1 名地點，建議：${measureList(first).filter(([t]) => t !== "路檢時段").map(([t]) => t).join("、") || "加強路檢"}。` : "")
    : `${where}沒有公開的毒駕熱點網格。`;
  $("headline").innerHTML = stats.map(([v, k, small]) => `<div class="stat${small ? " small" : ""}"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("") +
    `<p class="profile">${summary}</p>
     <p class="badge-privacy">隱私預算 +0：只用已公開的差分隱私結果與公開資料</p>`;
}

function renderPreventLegend() {
  const m = prevention.meta;
  const tiers = [
    [1, `毒駕事故扣掉雜訊誤差後仍 ≥ ${m.tier1Min} 件，且有死亡事故或路口事故集中`],
    [2, "有測速照相、路口、夜間或死亡等任一改善缺口"],
    [3, "沒有明顯缺口，於事故高峰時段加強路檢即可"],
  ];
  $("legend").innerHTML = `<h2>毒駕高風險地點（顏色＝優先等級，數字＝毒駕風險排名）</h2>
    ${tiers.map(([t, rule]) => `<div class="spot-key"><i class="prevent" style="background:${TIERS[t].color}"></i><span><b>${TIERS[t].label}・${TIERS[t].short}</b>：${rule}</span></div>`).join("")}
    <div class="spot-key"><i class="camera-dot"></i>現有固定測速照相（點進縣市後顯示）</div>
    <p class="criteria">建議措施依格內公開事故判斷：沒有固定測速照相 → 測速照相；路口事故 ≥ ${m.junctionMin} 件 → 路口科技執法；夜間事故比例 ≥ 全台 ${m.nightFactor} 倍 → 夜間照明與監測；有死亡 → 優先降速。毒駕件數含差分隱私雜訊（ε = ${m.epsilonGrid}），95% 的情況誤差在 ±${m.margin95} 件內。</p>`;
}

function renderPreventAreas() {
  // the list above already ranks counties (nationwide) or places (inside a county)
  $("list").innerHTML = "";
  $("list-title").textContent = "";
}

function preventAreaTip(county, town) {
  const r = town ? regions[county]?.towns[town] : regions[county];
  return r?.drug == null ? "毒駕事故件數過少，不公開" : `毒駕事故約 ${fmt(r.drug)} 件（加噪）`;
}

function renderPreventSources() {
  const m = prevention.meta;
  $("privacy").querySelector("summary").textContent = "預防建議怎麼算出來的？";
  $("privacy-body").innerHTML = `<ol>
    <li>地點只使用<b>已加差分隱私雜訊並公開</b>的縣市數字與毒駕 500m 網格，沒有再讀取毒駕個案資料，所以不增加隱私預算。專案的毒駕資料為模擬資料，這部分是方法示範。</li>
    <li>每個網格的事故件數、死傷、夜間比例、高峰時段與最常出事的路口，取自警政署公開的 <b>114 年傷亡道路交通事故資料</b>（${fmt(m.accidents)} 件座標可用）。</li>
    <li>測速照相取自警政署開放資料「<b>測速執法設置點</b>」（${fmt(m.cameraCount)} 支，只含固定式，不含移動式、區間測速與國道）。</li>
    <li>座標預設點（例如許多行政區的事故被登記在同一點）在加雜訊之前就已排除；另有 ${m.placeholderCells} 個網格的事故大多疊在同一個登記地址，無法代表真實地點，不列入建議。</li>
    <li>「95% 落在」的範圍由公開的加噪數字直接算出（Laplace 雜訊的分布已知），屬於後處理，不花隱私預算。</li>
    <li>這些是<b>優先評估的地點與方向</b>，實際措施仍需現場勘查與交通工程評估，不代表設置後一定能降低傷亡。</li>
  </ol>`;
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
  if (state.mode === "prevent") return renderPreventHeadline();
  const { county, town } = state;
  const el = $("headline");
  let rec, stats;
  if (state.mode === "all") {
    const p = profileOf(county, town);
    if (!p) { el.innerHTML = `<p class="sub">沒有這個地區的事故資料。</p>`; return; }
    el.innerHTML = [[fmt(p.total), "傷亡事故"], [fmt(p.deaths), "死亡人數", true], [fmt(p.injuries), "受傷人數", true], [fmt(p.a1), "A1 死亡事故", true]]
      .map(([v, k, small]) => `<div class="stat${small ? " small" : ""}"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("") +
      `<p class="profile">最常發生的時段：<b>${p.peakHours.map((h) => `${h}–${h + 1} 時`).join("、")}</b><br>
       肇事車種：${p.vehicles.map(([k, v]) => `<b>${k}</b> ${Math.round(v * 100)}%`).join("、")}</p>`;
    return;
  }
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
    const byCount = state.metric === "drug";
    stats = [
      byCount ? [fmt(rec.drug), "毒駕事故（加噪）"] : [fmt(rec.per10k, 1), "每萬件事故毒駕數"],
      byCount ? [fmt(rec.per10k, 1), "每萬件事故毒駕數", true] : [fmt(rec.drug), "毒駕事故（加噪）", true],
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
  if (state.mode === "prevent") return renderPreventLegend();
  const town = state.town;
  if (state.mode === "all") {
    const br = state.county ? allTownBreaks : allCountyBreaks;
    $("legend").innerHTML = `<h2>${town ? "所在鄉鎮" : state.county ? "各鄉鎮市區" : "各縣市"}傷亡事故件數</h2>
      <div class="bar">${RAMP.map((c) => `<span style="background:${c}"></span>`).join("")}</div>
      <div class="ticks">${br.map((b) => `<span>${fmt(b)}</span>`).join("")}<span></span></div>
      ${state.county ? `<div class="spot-key"><i>1</i>事故熱點（半徑 ${general.meta.spotRadius}m，約一個路口），數字是排名</div>` : `<div class="spot-key"><i>1</i>點進縣市或鄉鎮，顯示事故熱點</div>`}`;
    return;
  }
  const br = town ? CELL_BREAKS : (state.county ? townBreaks : countyBreaks)[state.metric];
  const title = town ? `500m 網格內毒駕事故數（加噪，≥ ${meta.minCellCount} 才公開）` : METRICS[state.metric].title;
  $("legend").innerHTML = `<h2>${title}</h2>
    <div class="bar">${RAMP.map((c) => `<span style="background:${c}"></span>`).join("")}</div>
    <div class="ticks">${br.map((b) => `<span>${b}</span>`).join("")}<span>${town ? "+" : ""}</span></div>
    ${town ? "" : `<div class="withheld"><i></i>少於 ${meta.minDistrictCount} 件，不公開</div>`}`;
}

function renderList() {
  if (state.mode === "prevent") return renderPreventAreas();
  const { county, town } = state;
  const ol = $("list");
  ol.innerHTML = "";
  let items;
  if (state.mode === "all") {
    if (town) { $("list-title").textContent = ""; return; }
    const source = county ? general.profiles.counties[county].towns : Object.fromEntries(Object.entries(general.profiles.counties).map(([k, v]) => [k, v.profile]));
    const br = county ? allTownBreaks : allCountyBreaks;
    $("list-title").textContent = county ? `${county}各鄉鎮市區傷亡事故（點選進入）` : "各縣市傷亡事故（點選進入）";
    items = Object.entries(source)
      .filter(([name]) => !county || regions[county]?.towns[name])
      .map(([name, p]) => ({ label: name, value: p.total, color: colorFor(p.total, br), shown: `${fmt(p.total)} 件`, act: () => (county ? go(county, name) : go(name)) }))
      .sort((a, b) => b.value - a.value);
  } else if (town) {
    $("list-title").textContent = "熱點網格（點選看街景）";
    items = (cells[`${county}|${town}`] || [])
      .map(([lat, lng, n], i) => ({ label: `網格 ${i + 1}`, value: n, color: colorFor(n, CELL_BREAKS), shown: `${n} 件`, act: () => openStreetView({ lat, lng, n, rank: i }) }))
      .sort((a, b) => b.value - a.value)
      .map((x, i) => ({ ...x, label: `熱點 ${i + 1}` }));
    if (!items.length) ol.innerHTML = `<li class="sub">這個鄉鎮沒有加噪後 ≥ ${meta.minCellCount} 件的網格。</li>`;
  } else {
    const source = county ? regions[county].towns : regions;
    const m = METRICS[state.metric];
    const br = (county ? townBreaks : countyBreaks)[state.metric];
    $("list-title").textContent = `${county ? `${county}各鄉鎮市區` : "各縣市"}${m.label}（點選進入）`;
    items = Object.entries(source)
      .map(([name, r]) => ({ label: name, value: r[state.metric], color: colorFor(r[state.metric], br),
        shown: r[state.metric] == null ? "不公開" : fmt(r[state.metric], m.digits) + m.unit,
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
  if (state.mode === "prevent") return renderPreventSources();
  if (state.mode === "all") {
    $("privacy").querySelector("summary").textContent = "一般事故的資料從哪來？";
    $("privacy-body").innerHTML = `<ol>
      <li>來源是警政署公開的 <b>114 年傷亡道路交通事故資料</b>，本來就是公開資料，所以這一層不加雜訊。</li>
      <li>事故熱點：從事故最密集的位置開始，把 <b>半徑 ${general.meta.spotRadius}m</b> 內的事故算成一個熱點（約一個路口），每個縣市與鄉鎮各列出前 ${general.meta.spotsPerArea} 名，圓圈畫在熱點內事故的中心位置。</li>
      <li>路名取自熱點附近最近的 Google 街景，不一定是事故登記的路名。</li>
      <li>有 ${fmt(general.meta.placeholder)} 件事故的座標不可靠（例如落在登記鄉鎮之外、或許多鄉鎮的事故疊在同一點），不列入熱點，但仍計入件數。</li>
    </ol>
    <p>這一層沒有毒駕資訊；毒駕事故請切換到「毒駕事故」。</p>`;
    return;
  }
  $("privacy").querySelector("summary").textContent = "資料怎麼保護隱私？";
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

function tipAll(label, p) {
  return p ? `<b>${label}</b>傷亡事故 ${fmt(p.total)} 件<br>死亡 ${fmt(p.deaths)}・受傷 ${fmt(p.injuries)}` : `<b>${label}</b>無資料`;
}

function tipCounty(name) {
  if (state.mode === "prevent") return `<b>${name}</b>${preventAreaTip(name)}`;
  if (state.mode === "all") return tipAll(name, profileOf(name));
  const r = regions[name];
  if (!r) return `<b>${name}</b>無資料`;
  return `<b>${name}</b>${r.per10k == null ? "件數過少，不公開" : `每萬件事故 ${fmt(r.per10k, 1)} 件毒駕<br>毒駕 ${fmt(r.drug)} / 事故 ${fmt(r.total)}`}`;
}

function tipTown(county, name) {
  if (state.mode === "prevent") return `<b>${county}${name}</b>${preventAreaTip(county, name)}`;
  if (state.mode === "all") return tipAll(`${county}${name}`, profileOf(county, name));
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
