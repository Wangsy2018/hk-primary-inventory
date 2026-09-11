"""
看板的「项目地图」页（map.html）：Leaflet + 政府地图瓦片，数据读 land_chain.json（land_chain.py 产出）。

独立一页而不是塞在看板底部：地图要整屏才好用，滚轮 / 拖动也不会和长页面打架。
看板顶部有 tab 切过来，这页顶部有 tab 切回去。
"""

MAP_CSS = """
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; }
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background: #f4f6f9; color: #2c3e50; display: flex; flex-direction: column; }
  .header { background: linear-gradient(135deg, #1f3a5f, #2980b9); color: #fff; padding: 14px 22px 0; }
  .header h1 { font-size: 20px; font-weight: 700; }
  .header .sub { font-size: 12px; opacity: 0.9; margin-top: 4px; }
  .tabs { display: flex; gap: 4px; margin-top: 10px; }
  .tabs a { color: #cfe0ff; text-decoration: none; font-size: 13px; padding: 7px 14px; border-radius: 8px 8px 0 0; background: rgba(255,255,255,.08); }
  .tabs a.on { background: #f4f6f9; color: #1f3a5f; font-weight: 700; }
  .mapbar { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; font-size: 13px; color: #334155; padding: 10px 22px 0; }
  .mapbar label { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; white-space: nowrap; }
  .mapbar select, .mapbar input[type=text] { border: 1px solid #dbe4ee; border-radius: 8px; padding: 5px 8px; font-size: 13px; background: #fff; }
  .mapbar input[type=text] { width: 190px; }
  .mapsum { font-size: 12px; color: #64748b; padding: 6px 22px 8px; }
  .map { flex: 1; min-height: 320px; background: #e8eef5; }
  .map-note { font-size: 11.5px; color: #94a3b8; padding: 6px 22px 8px; line-height: 1.6; }
  .leaflet-container { font-family: inherit; }
  .leaflet-popup-content { margin: 12px 14px; font-size: 12.5px; line-height: 1.5; min-width: 240px; max-width: 320px; }
  .leaflet-popup-content h4 { margin: 0 0 2px; font-size: 15px; color: #1f3a5f; }
  .leaflet-popup-content .en { color: #64748b; font-size: 12px; margin-bottom: 4px; }
  .leaflet-popup-content .tags span { display: inline-block; font-size: 10.5px; padding: 1px 7px; border-radius: 20px;
                                      background: #eef2f7; color: #334155; margin: 0 4px 6px 0; font-weight: 600; }
  table.chain { border-collapse: collapse; width: 100%; margin-top: 2px; }
  table.chain td { padding: 3px 4px; border-top: 1px solid #f1f5f9; vertical-align: top; }
  table.chain td:first-child { color: #7f8c8d; white-space: nowrap; width: 38px; }
  table.chain td b { color: #1f3a5f; }
  .leaflet-popup-content details { margin-top: 4px; }
  .leaflet-popup-content summary { cursor: pointer; color: #1f6feb; font-size: 12px; }
  .leaflet-popup-content .addr { color: #64748b; font-size: 11.5px; margin-top: 6px; }
  .map-legend { background: rgba(255,255,255,.93); padding: 8px 10px; border-radius: 8px; font-size: 11.5px; line-height: 1.7; box-shadow: 0 1px 4px rgba(0,0,0,.15); }
  .map-legend i { display: inline-block; width: 11px; height: 11px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; }
  .map-legend .st { margin-top: 4px; border-top: 1px solid #e2e8f0; padding-top: 4px; }
  @media (max-width: 800px) {
    .header { padding: 10px 14px 0; } .header h1 { font-size: 17px; } .header .sub { display: none; }
    .mapbar { padding: 8px 12px 0; gap: 4px 10px; font-size: 12px; } .mapbar input[type=text] { width: 100%; }
    .mapsum, .map-note { padding-left: 12px; padding-right: 12px; } .map-note { display: none; }
    .map-legend { font-size: 10.5px; line-height: 1.5; }
  }
"""

MAP_HTML = """
  <div class="header">
    <h1>🗺️ 项目地图：地 → 楼 → 售</h1>
    <div class="sub">地政总署卖地 / 换地 / 契约修订 · 屋宇署批则 / 动工 / 入伙 · 预售同意书 —— 官方记录按坐标与地段号串联 · 更新时间 __LAST_UPDATE__</div>
    <div class="tabs"><a href="./">📊 行情看板</a><a class="on" href="map.html">🗺️ 项目地图</a></div>
  </div>
  <div class="mapbar" id="map-sec">
    <label><input type="checkbox" data-status="在售" checked> 在售</label>
    <label><input type="checkbox" data-status="已批预售·未开售" checked> 已批预售·未开售</label>
    <label><input type="checkbox" data-status="已动工·未预售" checked> 已动工·未预售</label>
    <label><input type="checkbox" data-status="政府已卖地·未开上盖" checked> 政府已卖地·未开上盖</label>
    <label><input type="checkbox" data-status="换地补价·未开上盖" checked> 换地/补价·未开上盖</label>
    <label><input type="checkbox" data-status="已售罄·已入伙"> 已售罄·已入伙</label>
    <label><input type="checkbox" data-status="已入伙·未预售"> 已入伙·未预售</label>
    <select id="map-src"><option value="">全部土地来源</option></select>
    <select id="map-min">
      <option value="0">全部规模</option><option value="100">≥ 100 伙</option>
      <option value="300">≥ 300 伙</option><option value="1000">≥ 1,000 伙</option>
    </select>
    <select id="map-size" title="圆点大小按什么算">
      <option value="units">大小：总伙数</option><option value="remaining">大小：余货</option>
    </select>
    <input type="text" id="map-q" placeholder="搜项目名 / 地址 / 地段">
  </div>
  <div class="mapsum" id="map-sum">地图数据加载中…</div>
  <div id="map" class="map"></div>
  <div class="map-note">
    圆点大小默认按总伙数，只勾「在售」时自动改按 house730 余货（也可手动切）；颜色是土地来源：<b>公开卖地</b>（地政总署卖地记录）、<b>换地 / 契约修订补地价</b>（已签立换地、契约修订记录）、
    <b>港铁上盖</b>（预售卖方为港铁 / 九铁物业公司）、<b>市建局</b>、<b>房協</b>。实心 = 在售（house730 有余货）；黑边 = 已批预售但 house730 未见开售；
    虚边 = 屋宇署已发施工同意书但未批预售；空心 = 已批地（政府卖地 / 换地补价）但屋宇署未发上盖施工同意书，大小按地盘面积；淡色 = 已售罄 / 已入伙。各图层之间没有共同编号，靠坐标（30 米内）和地段号对上，大型屋苑一个点对应多个屋宇署地盘，
    伙数以预售同意书为准、屋宇署数字作参考；少数记录官方坐标有误已剔除。点圆点看这块地从批地到入伙的每一步。
  </div>
"""

MAP_JS = r"""
<script>
(function () {
  var sec = document.getElementById('map-sec');
  if (!sec) return;
  var SRC_COLOR = {
    '公开卖地': '#1f6feb', '换地补地价': '#f59e0b', '契约修订补地价': '#a855f7', '港铁上盖': '#e11d48',
    '市建局': '#059669', '房協': '#0891b2', '愉景湾': '#64748b', '未知': '#9ca3af'
  };
  var SRC_ORDER = ['公开卖地', '换地补地价', '契约修订补地价', '港铁上盖', '市建局', '房協', '愉景湾', '未知'];
  var STATUS_STYLE = {
    '在售':          { fillOpacity: 0.85, weight: 1.2 },
    '已批预售·未开售': { fillOpacity: 0.85, weight: 2.5, color: '#111' },
    '已批预售':       { fillOpacity: 0.85, weight: 1.2 },
    '已售罄·已入伙':   { fillOpacity: 0.25, weight: 1 },
    '已动工·未预售':   { fillOpacity: 0.55, weight: 2, dashArray: '3,3' },
    '已入伙·未预售':   { fillOpacity: 0.20, weight: 1, dashArray: '3,3' },
    '政府已卖地·未开上盖': { fillOpacity: 0.0, weight: 2.4 },
    '换地补价·未开上盖':   { fillOpacity: 0.0, weight: 2.4 }
  };
  var STATUS_ORDER = ['在售', '已批预售·未开售', '已批预售', '已动工·未预售', '政府已卖地·未开上盖', '换地补价·未开上盖', '已售罄·已入伙', '已入伙·未预售'];
  var map, layer, data, baseNote = '';
  var sumEl = document.getElementById('map-sum');

  function note(t) { sumEl.textContent = t; }

  function loadLib() {
    var css = document.createElement('link');
    css.rel = 'stylesheet'; css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    document.head.appendChild(css);
    var js = document.createElement('script');
    js.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    js.onload = init;
    js.onerror = function () { note('地图库加载失败（需要联网）'); };
    document.head.appendChild(js);
  }
  loadLib();

  function fmtUnits(n) { return n == null ? '—' : Number(n).toLocaleString('en-US'); }
  function fmtPremium(m) {
    if (m == null) return '';
    if (m >= 100) return (m / 100).toFixed(1) + ' 亿';
    if (m >= 1) return m.toFixed(0) + ' 百万';
    return (m * 100).toFixed(0) + ' 万';
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function popupHtml(s) {
    var h = '<h4>' + esc(s.name) + '</h4>';
    if (s.name_en && s.name_en !== s.name) h += '<div class="en">' + esc(s.name_en) + '</div>';
    h += '<div class="tags"><span style="background:' + (SRC_COLOR[s.source] || '#999') + '22;color:' + (SRC_COLOR[s.source] || '#333') + '">' + esc(s.source) + '</span>';
    if (s.owner && s.owner !== '私人') h += '<span>' + esc(s.owner) + '</span>';
    h += '<span>' + esc(s.status) + '</span></div>';
    h += '<table class="chain">';
    if (s.land && s.land.length) {
      s.land.slice(0, 3).forEach(function (r) {
        var t = '<b>' + esc(r.kind) + '</b> ' + esc(r.date);
        if (r.premium_m != null && r.premium_m > 0) t += ' · 地价 ' + fmtPremium(r.premium_m);
        if (r.area) t += ' · ' + fmtUnits(r.area) + ' ㎡';
        if (r.lot) t += '<br><span style="color:#94a3b8">' + esc(r.lot) + '</span>';
        if (r.party) t += '<br><span style="color:#94a3b8">' + esc(r.party) + '</span>';
        h += '<tr><td>批地</td><td>' + t + '</td></tr>';
      });
      if (s.land.length > 3) h += '<tr><td></td><td style="color:#94a3b8">另 ' + (s.land.length - 3) + ' 条记录</td></tr>';
    } else {
      h += '<tr><td>批地</td><td style="color:#94a3b8">地政总署卖地 / 换地 / 契约修订库中无记录</td></tr>';
    }
    if (s.plan_ym) h += '<tr><td>批则</td><td>' + esc(s.plan_ym) + '</td></tr>';
    if (s.stage === '批地未动工') h += '<tr><td>上盖</td><td style="color:#94a3b8">屋宇署未发上盖施工同意书</td></tr>';
    if (s.start_ym) h += '<tr><td>动工</td><td>' + esc(s.start_ym) + (s.bd_units != null ? ' · <b>' + fmtUnits(s.bd_units) + '</b> 伙' : '') +
      (s.bd_sites > 1 ? '（' + s.bd_sites + ' 个屋宇署地盘）' : '') + '</td></tr>';
    else if (s.stage !== '批地未动工') h += '<tr><td>动工</td><td style="color:#94a3b8">屋宇署无施工同意书记录</td></tr>';
    if (s.presale_first) {
      var ph = s.presale_first === s.presale_last ? s.presale_first : s.presale_first + ' ~ ' + s.presale_last;
      h += '<tr><td>预售</td><td>' + esc(ph) + ' · <b>' + fmtUnits(s.presale_units) + '</b> 伙';
      if (s.phases && s.phases.length > 1) {
        h += '<details><summary>' + s.phases.length + ' 期明细</summary>';
        s.phases.forEach(function (p) { h += '<div>' + esc(p.ym) + ' · ' + esc(p.name) + ' · ' + fmtUnits(p.units) + ' 伙</div>'; });
        h += '</details>';
      }
      h += '</td></tr>';
    }
    if (s.sale) {
      h += '<tr><td>销售</td><td>' + (s.sale.first_sales ? '开售 ' + esc(s.sale.first_sales) + ' · ' : '') +
        '已售 <b>' + fmtUnits(s.sale.sold) + '</b> / ' + fmtUnits(s.sale.total) + ' · 余 <b>' + fmtUnits(s.sale.remaining) + '</b> 伙' +
        '<br><span style="color:#94a3b8">house730：' + esc(s.sale.projects.join('、')) + '</span></td></tr>';
    }
    if (s.op_ym) h += '<tr><td>入伙</td><td>' + esc(s.op_ym) + ' · <b>' + fmtUnits(s.op_units) + '</b> 伙</td></tr>';
    h += '</table>';
    var who = [];
    if (s.applicant) who.push('申请人 ' + s.applicant.replace(/<br\s*\/?>/g, ' / '));
    if (s.vendor) who.push('卖方 ' + s.vendor);
    if (s.ap) who.push('认可人士 ' + s.ap);
    if (s.address) who.unshift(s.address);
    if (who.length) h += '<div class="addr">' + esc(who.join(' · ')) + '</div>';
    return h;
  }

  var sizeBy = 'units';
  function sizeValue(s) {
    if (sizeBy === 'remaining') return s.sale ? s.sale.remaining : 0;
    return s.presale_units || s.bd_units || 0;
  }
  function radius(s) {
    var u = sizeValue(s);
    if (!u && s.stage === '批地未动工' && s.area) return Math.max(4, Math.min(20, Math.sqrt(s.area) / 9));   // 没伙数，按地盘面积
    if (!u) return sizeBy === 'remaining' ? 3 : 5;
    return Math.max(4, Math.min(24, Math.sqrt(u) / (sizeBy === 'remaining' ? 1.3 : 2.2)));
  }

  function currentFilter() {
    var st = {};
    sec.querySelectorAll('input[data-status]').forEach(function (c) { st[c.getAttribute('data-status')] = c.checked; });
    st['已批预售'] = st['已批预售·未开售'] || st['在售'];     // 没有 house730 表时的兜底状态
    return {
      status: st,
      src: document.getElementById('map-src').value,
      min: +document.getElementById('map-min').value,
      q: document.getElementById('map-q').value.trim().toLowerCase()
    };
  }

  function render() {
    if (!map || !data) return;
    var f = currentFilter();
    layer.clearLayers();
    var shown = [], units = {}, cnt = {}, remaining = 0, premium = {};
    data.sites.forEach(function (s) {
      if (!f.status[s.status]) return;
      if (f.src && s.source !== f.src) return;
      var u = s.presale_units || s.bd_units || 0;
      if (f.min && u < f.min) return;
      if (f.q) {
        var hay = (s.name + ' ' + s.name_en + ' ' + s.address + ' ' + (s.land || []).map(function (r) { return r.lot; }).join(' ')).toLowerCase();
        if (hay.indexOf(f.q) < 0) return;
      }
      shown.push(s);
      cnt[s.status] = (cnt[s.status] || 0) + 1;
      units[s.status] = (units[s.status] || 0) + u;
      if (s.sale) remaining += s.sale.remaining;
      if (s.premium_m) premium[s.status] = (premium[s.status] || 0) + s.premium_m;
    });
    // 大的先画在下面，小的在上面，免得被盖住点不到
    shown.sort(function (a, b) { return radius(b) - radius(a); });
    shown.forEach(function (s) {
      var st = STATUS_STYLE[s.status] || {};
      var m = L.circleMarker([s.lat, s.lon], {
        radius: radius(s), color: st.color || SRC_COLOR[s.source] || '#999', fillColor: SRC_COLOR[s.source] || '#999',
        fillOpacity: st.fillOpacity, weight: st.weight, dashArray: st.dashArray || null, opacity: 0.95
      });
      m.bindPopup(function () { return popupHtml(s); }, { maxWidth: 340 });
      var tip = s.name + (s.presale_units || s.bd_units ? ' · ' + fmtUnits(s.presale_units || s.bd_units) + ' 伙' : '');
      if (s.sale && s.sale.remaining > 0) tip += ' · 余 ' + fmtUnits(s.sale.remaining);
      m.bindTooltip(tip, { direction: 'top', offset: [0, -4] });
      layer.addLayer(m);
    });
    var parts = [];
    STATUS_ORDER.forEach(function (k) {
      if (!cnt[k]) return;
      parts.push(k + ' ' + cnt[k] + (premium[k] ? ' 幅（地价 ' + (premium[k] / 100).toFixed(0) + ' 亿）' : ' 个' + (units[k] ? '（' + fmtUnits(units[k]) + ' 伙）' : '')));
    });
    note('显示 ' + shown.length + ' 个地盘：' + (parts.join(' · ') || '无') +
      (remaining ? ' · 余货合计 ' + fmtUnits(remaining) + ' 伙' : '') +
      ' · 预售至 ' + data.as_of.presale + ' · 屋宇署至 ' + data.as_of.bd_start + ' · 土地记录至 ' + data.as_of.land + baseNote);
    if (f.q && shown.length && shown.length <= 30) {
      map.fitBounds(L.latLngBounds(shown.map(function (s) { return [s.lat, s.lon]; })).pad(0.3), { maxZoom: 15 });
    }
  }

  function init() {
    map = L.map('map', { center: [22.36, 114.13], zoom: 11, preferCanvas: true });
    var attrGov = '地圖資料 &copy; <a href="https://www.landsd.gov.hk" target="_blank" rel="noopener">地政總署</a> / CSDI';
    var GOV = 'https://mapapi.geodata.gov.hk/gs/api/v1.0.0/xyz/';
    var gov = L.tileLayer(GOV + 'basemap/WGS84/{z}/{x}/{y}.png', { maxZoom: 19, attribution: attrGov });
    var govLabel = L.tileLayer(GOV + 'label/hk/tc/WGS84/{z}/{x}/{y}.png', { maxZoom: 19, pane: 'shadowPane' });
    var imagery = L.tileLayer(GOV + 'imagery/WGS84/{z}/{x}/{y}.png', { maxZoom: 19, attribution: attrGov });
    var osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap' });
    var base = L.layerGroup([gov, govLabel]);
    var sat = L.layerGroup([imagery, govLabel]);
    // 先探一张政府瓦片：通就默认政府地图并提供卫星图；不通（内地代理常挡 gov.hk）只给 OSM，
    // 免得切到政府地图对着灰底
    var probe = new Image(), decided = false;
    L.control.layers({ '政府地图': base, '卫星图': sat, 'OpenStreetMap': osm }, null, { position: 'topright' }).addTo(map);
    function useGov() {
      if (decided) return; decided = true;
      base.addTo(map);
    }
    function useOsm() {
      if (decided) return; decided = true;
      osm.addTo(map);
      baseNote = ' · 政府地图瓦片（gov.hk）当前网络不可达，已用 OpenStreetMap；右上角可手动切换';
      if (data) render();
    }
    probe.onload = useGov; probe.onerror = useOsm;
    setTimeout(useOsm, 4000);
    probe.src = GOV + 'basemap/WGS84/11/1673/893.png';

    layer = L.layerGroup().addTo(map);

    var legend = L.control({ position: 'bottomleft' });
    legend.onAdd = function () {
      var d = L.DomUtil.create('div', 'map-legend');
      d.innerHTML = SRC_ORDER.filter(function (k) { return k !== '未知'; }).map(function (k) {
        return '<div><i style="background:' + SRC_COLOR[k] + '"></i>' + k + '</div>';
      }).join('') +
        '<div class="st"><i style="background:#334155"></i>实心 在售 &nbsp; <i style="background:#334155;border:2px solid #111"></i>黑边 已批预售未开售<br>' +
        '<i style="border:2px dashed #334155;background:#33415588"></i>虚边 动工未预售 &nbsp; <i style="border:2px solid #334155"></i>空心 已批地未开上盖（大小按面积）</div>';
      return d;
    };
    legend.addTo(map);

    fetch('land_chain.json', { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) {
        data = j;
        var sel = document.getElementById('map-src');
        SRC_ORDER.forEach(function (k) {
          if (!data.sites.some(function (s) { return s.source === k; })) return;
          var o = document.createElement('option'); o.value = k; o.textContent = k; sel.appendChild(o);
        });
        render();
      })
      .catch(function (e) { note('地图数据 land_chain.json 加载失败：' + e.message); });

    var sizeSel = document.getElementById('map-size');
    sizeSel.addEventListener('change', function () { sizeBy = this.value; render(); });
    sec.querySelectorAll('input[data-status]').forEach(function (el) {
      el.addEventListener('change', function () {
        // 只看在售时，圆点按余货算才有意义；勾回其他状态就回到总伙数
        var on = Array.prototype.filter.call(sec.querySelectorAll('input[data-status]'), function (c) { return c.checked; })
          .map(function (c) { return c.getAttribute('data-status'); });
        sizeBy = (on.length === 1 && on[0] === '在售') ? 'remaining' : 'units';
        sizeSel.value = sizeBy;
        render();
      });
    });
    sec.querySelectorAll('#map-src, #map-min').forEach(function (el) { el.addEventListener('change', render); });
    var t;
    document.getElementById('map-q').addEventListener('input', function () { clearTimeout(t); t = setTimeout(render, 250); });
  }
})();
</script>
"""


def build_map_page(last_update: str) -> str:
    """整页 map.html。"""
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-HK\">\n<head>\n"
        "<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>项目地图 · 香港一手住宅行情看板</title>\n"
        "<link rel=\"manifest\" href=\"manifest.json\">\n"
        "<link rel=\"icon\" type=\"image/png\" sizes=\"192x192\" href=\"assets/pwa/icon-192.png\">\n"
        "<style>" + MAP_CSS + "</style>\n</head>\n<body>\n"
        + MAP_HTML.replace("__LAST_UPDATE__", last_update)
        + MAP_JS + "\n</body>\n</html>\n"
    )
