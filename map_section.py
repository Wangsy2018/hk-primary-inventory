"""
看板的「项目地图」块：Leaflet + 政府地图瓦片，数据读 land_chain.json（land_chain.py 产出）。

单独放一个文件，是为了不把 chart_dashboard 那个 f-string 模板越堆越大；
这里的三段是普通字符串，花括号不用转义。
"""

MAP_CSS = """
  .map-wrap { position: relative; }
  .map-wrap.full { position: fixed; inset: 0; z-index: 3000; background: #fff; padding: 12px; overflow: auto; }
  .map-wrap.full .map { height: calc(100vh - 150px); }
  .mapbar { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; font-size: 13px; color: #334155; margin: 6px 0 8px; }
  .mapbar label { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; white-space: nowrap; }
  .mapbar select, .mapbar input[type=text] { border: 1px solid #dbe4ee; border-radius: 8px; padding: 5px 8px; font-size: 13px; background: #fff; }
  .mapbar input[type=text] { width: 180px; }
  .mapbar button { border: 1px solid #dbe4ee; background: #f8fafc; border-radius: 8px; padding: 5px 10px; font-size: 13px; cursor: pointer; }
  .mapbar button:hover { background: #eef2f7; }
  .mapsum { font-size: 12px; color: #64748b; margin-bottom: 6px; }
  .map { height: 560px; border-radius: 10px; background: #e8eef5; }
  .map-note { font-size: 12px; color: #94a3b8; margin-top: 8px; line-height: 1.6; }
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
  @media (max-width: 800px) { .map { height: 70vh; } .mapbar input[type=text] { width: 100%; } }
"""

MAP_HTML = """
    <div class="chart-card" id="map-sec">
      <div class="map-wrap" id="map-wrap">
        <h2>项目地图：地 → 楼 → 售（地政总署 / 屋宇署官方记录按坐标串联）</h2>
        <div class="mapbar">
          <label><input type="checkbox" data-stage="已预售" checked> 已预售·未入伙</label>
          <label><input type="checkbox" data-stage="动工未预售" checked> 已动工·未预售</label>
          <label><input type="checkbox" data-stage="批地未动工" checked> 已批地·未动工</label>
          <label><input type="checkbox" data-stage="已预售·已入伙"> 已预售·已入伙</label>
          <label><input type="checkbox" data-stage="已入伙·未预售"> 已入伙·未预售</label>
          <select id="map-src"><option value="">全部土地来源</option></select>
          <select id="map-min">
            <option value="0">全部规模</option><option value="100">≥ 100 伙</option>
            <option value="300">≥ 300 伙</option><option value="1000">≥ 1,000 伙</option>
          </select>
          <input type="text" id="map-q" placeholder="搜项目名 / 地址 / 地段">
          <button id="map-full" type="button">⛶ 全屏</button>
        </div>
        <div class="mapsum" id="map-sum">地图数据加载中…</div>
        <div id="map" class="map"></div>
        <div class="map-note" id="map-note">
          圆点大小按伙数；颜色是土地来源：<b>公开卖地</b>（地政总署卖地记录）、<b>换地 / 契约修订补地价</b>（已签立换地、契约修订记录）、
          <b>港铁上盖</b>（预售卖方为港铁 / 九铁物业公司）、<b>市建局</b>、<b>房協</b>。实心 = 已批预售；虚边 = 屋宇署已发施工同意书但未批预售；
          空心 = 已批地但屋宇署未发施工同意书。各图层之间没有共同编号，靠坐标（30 米内）和地段号对上，大型屋苑一个点对应多个屋宇署地盘，
          伙数以预售同意书为准、屋宇署数字作参考；少数记录官方坐标有误已剔除。点圆点看这块地从批地到入伙的每一步。
        </div>
      </div>
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
  var STAGE_STYLE = {
    '已预售':       { fillOpacity: 0.85, weight: 1.2 },
    '已预售·已入伙': { fillOpacity: 0.30, weight: 1 },
    '动工未预售':   { fillOpacity: 0.55, weight: 2, dashArray: '3,3' },
    '已入伙·未预售': { fillOpacity: 0.20, weight: 1, dashArray: '3,3' },
    '批地未动工':   { fillOpacity: 0.0,  weight: 2.2 }
  };
  var map, layer, data, booted = false;
  var sumEl = document.getElementById('map-sum');

  function note(t) { sumEl.textContent = t; }

  function loadLib() {
    if (booted) return; booted = true;
    var css = document.createElement('link');
    css.rel = 'stylesheet'; css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    document.head.appendChild(css);
    var js = document.createElement('script');
    js.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    js.onload = init;
    js.onerror = function () { note('地图库加载失败（需要联网）'); };
    document.head.appendChild(js);
  }
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (es) {
      if (es.some(function (e) { return e.isIntersecting; })) loadLib();
    }, { rootMargin: '600px' }).observe(sec);
  } else { loadLib(); }

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
    h += '<span>' + esc(s.stage) + '</span></div>';
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

  function radius(s) {
    var u = s.presale_units || s.bd_units || 0;
    if (!u) return 5;
    return Math.max(4, Math.min(24, Math.sqrt(u) / 2.2));
  }

  function currentFilter() {
    var stages = {};
    sec.querySelectorAll('input[data-stage]').forEach(function (c) { stages[c.getAttribute('data-stage')] = c.checked; });
    return {
      stages: stages,
      src: document.getElementById('map-src').value,
      min: +document.getElementById('map-min').value,
      q: document.getElementById('map-q').value.trim().toLowerCase()
    };
  }

  function render() {
    if (!map || !data) return;
    var f = currentFilter();
    layer.clearLayers();
    var shown = [], units = {}, cnt = {};
    data.sites.forEach(function (s) {
      if (!f.stages[s.stage]) return;
      if (f.src && s.source !== f.src) return;
      var u = s.presale_units || s.bd_units || 0;
      if (f.min && u < f.min) return;
      if (f.q) {
        var hay = (s.name + ' ' + s.name_en + ' ' + s.address + ' ' + (s.land || []).map(function (r) { return r.lot; }).join(' ')).toLowerCase();
        if (hay.indexOf(f.q) < 0) return;
      }
      shown.push(s);
      cnt[s.stage] = (cnt[s.stage] || 0) + 1;
      units[s.stage] = (units[s.stage] || 0) + u;
    });
    // 大的先画在下面，小的在上面，免得被盖住点不到
    shown.sort(function (a, b) { return radius(b) - radius(a); });
    shown.forEach(function (s) {
      var st = STAGE_STYLE[s.stage] || {};
      var m = L.circleMarker([s.lat, s.lon], {
        radius: radius(s), color: SRC_COLOR[s.source] || '#999', fillColor: SRC_COLOR[s.source] || '#999',
        fillOpacity: st.fillOpacity, weight: st.weight, dashArray: st.dashArray || null, opacity: 0.95
      });
      m.bindPopup(function () { return popupHtml(s); }, { maxWidth: 340 });
      m.bindTooltip(s.name + (s.presale_units || s.bd_units ? ' · ' + fmtUnits(s.presale_units || s.bd_units) + ' 伙' : ''), { direction: 'top', offset: [0, -4] });
      layer.addLayer(m);
    });
    var parts = [];
    ['已预售', '动工未预售', '批地未动工', '已预售·已入伙', '已入伙·未预售'].forEach(function (k) {
      if (!cnt[k]) return;
      parts.push(k + ' ' + cnt[k] + ' 个' + (units[k] ? '（' + fmtUnits(units[k]) + ' 伙）' : ''));
    });
    note('显示 ' + shown.length + ' 个地盘：' + (parts.join(' · ') || '无') +
      ' · 预售至 ' + data.as_of.presale + ' · 屋宇署至 ' + data.as_of.bd_start + ' · 土地记录至 ' + data.as_of.land);
    if (f.q && shown.length && shown.length <= 30) {
      map.fitBounds(L.latLngBounds(shown.map(function (s) { return [s.lat, s.lon]; })).pad(0.3), { maxZoom: 15 });
    }
  }

  function init() {
    map = L.map('map', { center: [22.36, 114.13], zoom: 11, preferCanvas: true, zoomControl: true });
    var attrGov = '地圖資料 &copy; <a href="https://www.landsd.gov.hk" target="_blank" rel="noopener">地政總署</a> / CSDI';
    var gov = L.tileLayer('https://mapapi.geodata.gov.hk/gs/api/v1.0.0/xyz/basemap/WGS84/{z}/{x}/{y}.png', { maxZoom: 19, attribution: attrGov });
    var govLabel = L.tileLayer('https://mapapi.geodata.gov.hk/gs/api/v1.0.0/xyz/label/hk/tc/WGS84/{z}/{x}/{y}.png', { maxZoom: 19, pane: 'shadowPane' });
    var imagery = L.tileLayer('https://mapapi.geodata.gov.hk/gs/api/v1.0.0/xyz/imagery/WGS84/{z}/{x}/{y}.png', { maxZoom: 19, attribution: attrGov });
    var osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap' });
    var base = L.layerGroup([gov, govLabel]).addTo(map);
    var sat = L.layerGroup([imagery, govLabel]);
    L.control.layers({ '政府地图': base, '卫星图': sat, 'OpenStreetMap': osm }, null, { position: 'topright' }).addTo(map);
    // 政府瓦片挂了就换 OSM
    var failed = 0;
    gov.on('tileerror', function () { if (++failed === 6) { map.removeLayer(base); osm.addTo(map); } });

    layer = L.layerGroup().addTo(map);

    var legend = L.control({ position: 'bottomleft' });
    legend.onAdd = function () {
      var d = L.DomUtil.create('div', 'map-legend');
      d.innerHTML = SRC_ORDER.filter(function (k) { return k !== '未知'; }).map(function (k) {
        return '<div><i style="background:' + SRC_COLOR[k] + '"></i>' + k + '</div>';
      }).join('') +
        '<div class="st"><i style="background:#334155"></i>实心 已预售 &nbsp; <i style="border:2px dashed #334155;background:#33415588"></i>虚边 动工未预售 &nbsp; <i style="border:2px solid #334155"></i>空心 批地未动工</div>';
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

    sec.querySelectorAll('input[data-stage], #map-src, #map-min').forEach(function (el) { el.addEventListener('change', render); });
    var t;
    document.getElementById('map-q').addEventListener('input', function () { clearTimeout(t); t = setTimeout(render, 250); });
    var wrap = document.getElementById('map-wrap');
    document.getElementById('map-full').addEventListener('click', function () {
      wrap.classList.toggle('full');
      this.textContent = wrap.classList.contains('full') ? '✕ 退出全屏' : '⛶ 全屏';
      setTimeout(function () { map.invalidateSize(); }, 60);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && wrap.classList.contains('full')) document.getElementById('map-full').click();
    });
  }
})();
</script>
"""
