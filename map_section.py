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
  .mapbar .fgrp { display: flex; align-items: center; flex-wrap: wrap; gap: 2px 8px;
                  padding: 2px 8px; border: 1px solid #e2e8f0; border-radius: 7px; background: #fff; }
  .mapbar .fgrp b { color: #64748b; font-weight: 600; margin-right: 2px; }
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
    <div class="fgrp" data-dim="src"><b>土地来源</b><label><input type="checkbox" data-all checked> 全部</label></div>
    <div class="fgrp" data-dim="presale"><b>预售</b>
      <label><input type="checkbox" data-all checked> 全部</label>
      <label><input type="checkbox" value="未申请" checked> 未申请</label>
      <label><input type="checkbox" value="已申请" checked> 已申请</label>
      <label><input type="checkbox" value="已批准" checked> 已批准</label>
    </div>
    <div class="fgrp" data-dim="sale"><b>销售</b>
      <label><input type="checkbox" data-all> 全部</label>
      <label><input type="checkbox" value="未售" checked> 未售</label>
      <label><input type="checkbox" value="在售" checked> 在售</label>
      <label><input type="checkbox" value="售罄"> 售罄</label>
    </div>
    <div class="fgrp" data-dim="build"><b>建设</b>
      <label><input type="checkbox" data-all checked> 全部</label>
      <label><input type="checkbox" value="未开工" checked> 未开工</label>
      <label><input type="checkbox" value="在建" checked> 在建</label>
      <label><input type="checkbox" value="已入伙" checked> 已入伙</label>
    </div>
    <div class="fgrp">
      <select id="map-min">
        <option value="0">全部规模</option><option value="100">≥ 100 伙</option>
        <option value="300">≥ 300 伙</option><option value="1000">≥ 1,000 伙</option>
      </select>
      <label title="港铁上盖、NOVO LAND 这类一块地分很多期卖的盘，按项目拆开（第 XIII 期的 A/B 子期合成一个项目，点开可见）"><input type="checkbox" id="map-split" checked> 按期拆分</label>
      <select id="map-size" title="圆点大小按什么算">
        <option value="units">大小：总伙数</option><option value="remaining">大小：余货</option>
      </select>
      <input type="text" id="map-q" placeholder="搜项目名 / 地址 / 地段">
    </div>
  </div>
  <div class="mapsum" id="map-sum">地图数据加载中…</div>
  <div id="map" class="map"></div>
  <div class="map-note">
    四个筛选各管一件事，互不干扰：<b>土地来源</b>（这块地怎么来的）、<b>预售</b>（未申请 / 已申请 = 在地政总署月报的待批名单里 / 已批准 = 已发预售同意书）、
    <b>销售</b>（未售 = 还没推出市场、在售 = house730 有余货、售罄 = 卖过或已落成而现在市面上没有）、<b>建设</b>（未开工 = 屋宇署未发上盖施工同意书、在建、已入伙 = 已发入伙纸）。
    颜色是土地来源；填充深浅看销售（实心在售、半透明未售、淡色售罄）；边框看建设（空心未开工、虚线在建、实线已入伙）；黑边是已批预售还没开卖。
    屋宇署月报 2011-06 起才有，更早开工的盘和现楼盘没有施工记录，建设状态按已知事实推断，弹窗里会注明。
    各图层之间没有共同编号，靠坐标（30 米内）和地段号对上，大型屋苑一个点对应多个屋宇署地盘；「预售批出」是全盘已批的伙数，「已推出」是 house730 收录到单位表的期数，两者差额即已批未推出。
    点圆点看这块地从批地到入伙的每一步。
  </div>
"""

MAP_JS = r"""
<script>
(function () {
  var sec = document.getElementById('map-sec');
  if (!sec) return;
  var SRC_COLOR = {
    '公开卖地': '#1f6feb', '换地补地价': '#f59e0b', '契约修订补地价': '#a855f7', '港铁上盖': '#e11d48',
    '市建局': '#059669', '无批地记录': '#a8b3c4'
  };
  var SRC_ORDER = ['公开卖地', '换地补地价', '契约修订补地价', '港铁上盖', '市建局', '无批地记录'];
  var SRC_NOTE = { '无批地记录': '2010 年前批的地，或现楼盘' };
  // 三个维度各管一种视觉：颜色=土地来源，填充深浅=销售，边框=建设。
  // 以前是一个 status 决定全部样式，加一档状态就得在 STATUS_STYLE 里补一条
  var FILL = { '在售': 0.85, '未售': 0.5, '售罄': 0.18 };
  var EDGE = {
    '未开工': { weight: 2.4, fill: 0 },              // 空心，大小按地盘面积
    '在建':   { weight: 2, dashArray: '3,3' },
    '已入伙': { weight: 1.2 }
  };
  var DIMS = {
    src: [],        // 土地来源的选项按数据动态生成
    presale: ['未申请', '已申请', '已批准'],
    sale: ['未售', '在售', '售罄'],
    build: ['未开工', '在建', '已入伙']
  };
  var DIM_KEY = { src: 'source', presale: 'f_presale', sale: 'f_sale', build: 'f_build' };

  function styleOf(s) {
    var e = EDGE[s.f_build] || {};
    var fill = e.fill != null ? e.fill : (FILL[s.f_sale] != null ? FILL[s.f_sale] : 0.5);
    // 已批预售还没开卖 —— 快推盘的盘，单独用黑边标出来
    var black = s.f_presale === '已批准' && s.f_sale === '未售';
    return { fillOpacity: fill, weight: black ? 2.5 : e.weight, dashArray: e.dashArray || null,
             color: black ? '#111' : null };
  }

  var map, layer, data, legendEl = null, baseNote = '';

  // 图例按当前地图上真有的来源生成 —— 写死的话会列出一个都没有的分类（房協），
  // 又漏掉占了一半地盘的「无批地记录」，看到满屏灰点却在图例里找不到
  function drawLegend(counts) {
    if (!legendEl) return;
    var rows = SRC_ORDER.filter(function (k) { return counts[k]; }).map(function (k) {
      return '<div><i style="background:' + SRC_COLOR[k] + '"></i>' + k +
        ' <span style="color:#94a3b8">' + counts[k] + (SRC_NOTE[k] ? ' · ' + SRC_NOTE[k] : '') + '</span></div>';
    }).join('');
    legendEl.innerHTML = rows +
      '<div class="st"><i style="background:#334155"></i>实心 在售 &nbsp;' +
      '<i style="background:#33415580"></i>半透明 未售 &nbsp;<i style="background:#33415530"></i>淡色 售罄<br>' +
      '<i style="border:2px solid #334155"></i>空心 未开工（大小按面积） &nbsp;' +
      '<i style="border:2px dashed #334155;background:#33415588"></i>虚线 在建 &nbsp;' +
      '<i style="background:#334155;border:2px solid #111"></i>黑边 已批预售未开卖</div>';
  }
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

  // 投标情况：中标价 + 收到几份标书 + 落标价。落标金额地政总署 2018/19 财年起才公布，
  // 市建局是签约后另发一篇稿公布，都不含中标价本身，所以条数 = 投标数 - 1。
  function bidHtml(n, under, won, others) {
    var t = '';
    if (n) t += '<br>收到 <b>' + n + '</b> 份标书';
    if (under && under.length) {
      var hi = under[0], lo = under[under.length - 1];
      t += (t ? ' · ' : '<br>') + '落标价 ' + fmtPremium(lo) + ' – ' + fmtPremium(hi);
      if (won && hi) t += ' <span style="color:#94a3b8">（中标高出次高 ' + ((won / hi - 1) * 100).toFixed(1) + '%）</span>';
      t += '<details><summary style="cursor:pointer;color:#64748b">全部落标价</summary>' +
           under.map(function (v, i) { return (i + 1) + '. ' + fmtPremium(v); }).join('<br>') + '</details>';
    } else if (n && n > 1) {
      t += ' <span style="color:#94a3b8">（未公布落标金额）</span>';
    }
    if (others) t += '<details><summary style="cursor:pointer;color:#64748b">其他投标者</summary>' +
                     esc(others).replace(/;\s*/g, '<br>') + '</details>';
    return t;
  }

  // 三维标签：预售 / 销售 / 建设，建设状态是推断的就标出来
  function tagsHtml(s) {
    var h = '<div class="tags"><span style="background:' + (SRC_COLOR[s.source] || '#999') + '22;color:' +
            (SRC_COLOR[s.source] || '#333') + '">' + esc(s.source) + '</span>';
    if (s.owner && s.owner !== '私人' && s.owner !== s.source) h += '<span>' + esc(s.owner) + '</span>';
    h += '<span>预售 ' + esc(s.f_presale) + '</span><span>销售 ' + esc(s.f_sale) + '</span>' +
         '<span>建设 ' + esc(s.f_build) + (s.f_build_from === '推断' ? '（推断）' : '') + '</span></div>';
    return h;
  }

  function popupHtml(s) {
    var h = '<h4>' + esc(s.name) + '</h4>';
    if (s._proj) {
      var p = s._proj, left = (p.units != null && p.sold != null) ? Math.max(0, p.units - p.sold) : null;
      h += '<div class="en">' + esc(s._parent.name) + ' 的一个项目' + ((p.subs || []).length > 1 ? '（含 ' + p.subs.length + ' 个子期）' : '') + '</div>';
      h += tagsHtml(s);
      h += '<table class="chain">';
      h += '<tr><td>本项目</td><td>' + (p.units != null ? '批出 <b>' + fmtUnits(p.units) + '</b> 伙 · ' : '') +
        '已售 <b>' + (p.sold != null ? fmtUnits(p.sold) : '—') + '</b>' + (left != null ? ' · 余 <b>' + fmtUnits(left) + '</b>' : '') +
        (p.units_partial ? '<br><span style="color:#94a3b8">部分子期未对上预售伙数，批出数偏少</span>' : '') +
        (p.first_print ? '<br><span style="color:#94a3b8">售楼书 ' + esc(p.first_print) + (p.last_pasp ? ' · 最近成交 ' + esc(p.last_pasp) : '') + '</span>' : '') +
        '</td></tr>';
      if (p.sold_partial) {
        h += '<tr><td>成交册</td><td style="color:#94a3b8">已售 ' + (p.sold != null ? fmtUnits(p.sold) + ' 宗' : '—') +
          '（《一手住宅物業銷售條例》2013-04-29 生效前开卖，条例前的成交不在册内，不据此算余货）</td></tr>';
      }
      if ((p.subs || []).length > 1) {
        h += '<tr><td>子期</td><td>' + p.subs.length + ' 个<details open><summary>' + p.subs.map(function (x) { return esc(x.phase); }).join(' / ') + '</summary>';
        p.subs.forEach(function (x) {
          var l2 = (x.units != null && x.sold != null && !x.sold_partial) ? Math.max(0, x.units - x.sold) : null;
          h += '<div>' + esc(x.phase) + ' · ' + (x.units != null ? fmtUnits(x.units) + ' 伙' : '—') +
            (x.sold != null ? ' · 已售 ' + fmtUnits(x.sold) : '') + (l2 ? ' · 余 ' + fmtUnits(l2) : '') +
            (x.active === 'Y' ? '' : ' <span style="color:#94a3b8">(售罄/停售)</span>') + '</div>';
        });
        h += '</details></td></tr>';
      }
      h += '<tr><td>整盘</td><td>' + esc(s._parent.name) + ' 共 ' + (s._parent.packages || []).length + ' 个项目在册 · 预售批出 <b>' + fmtUnits(s._parent.presale_units) + '</b> 伙</td></tr>';
      h += '</table><table class="chain">';
      var pa = s._parent;
      if (pa.land && pa.land.length) {
        var r = pa.land[0];
        h += '<tr><td>批地</td><td><b>' + esc(r.kind) + '</b> ' + esc(r.date) +
          (r.premium_m ? ' · 地价 ' + fmtPremium(r.premium_m) : '') +
          (r.party ? '<br><span style="color:#94a3b8">' + esc(r.party) + '</span>' : '') +
          bidHtml(r.n_tender, r.underbids_m, r.premium_m, r.others) + '</td></tr>';
      }
      if (pa.plan_ym) h += '<tr><td>批则</td><td>' + esc(pa.plan_ym) + '</td></tr>';
      if (pa.start_ym) h += '<tr><td>动工</td><td>' + esc(pa.start_ym) + (pa.bd_units != null ? ' · ' + fmtUnits(pa.bd_units) + ' 伙（整盘）' : '') + '</td></tr>';
      if (pa.op_ym) h += '<tr><td>入伙</td><td>' + esc(pa.op_ym) + ' · ' + fmtUnits(pa.op_units) + ' 伙（整盘）</td></tr>';
      h += '</table>';
      if (pa.address) h += '<div class="addr">' + esc(pa.address) + '</div>';
      return h;
    }
    if (s.name_en && s.name_en !== s.name) h += '<div class="en">' + esc(s.name_en) + '</div>';
    h += tagsHtml(s);
    h += '<table class="chain">';
    if (s.land && s.land.length) {
      s.land.slice(0, 3).forEach(function (r) {
        var t = '<b>' + esc(r.kind) + '</b> ' + esc(r.date);
        if (r.premium_m != null && r.premium_m > 0) t += ' · 地价 ' + fmtPremium(r.premium_m);
        if (r.area) t += ' · ' + fmtUnits(r.area) + ' ㎡';
        if (r.lot) t += '<br><span style="color:#94a3b8">' + esc(r.lot) + '</span>';
        if (r.party) t += '<br><span style="color:#94a3b8">' + esc(r.party) + '</span>';
        t += bidHtml(r.n_tender, r.underbids_m, r.premium_m, r.others);
        if (r.remark) t += '<br><span style="color:#b45309">' + esc(r.remark) + '</span>';
        h += '<tr><td>批地</td><td>' + t + '</td></tr>';
      });
      if (s.land.length > 3) h += '<tr><td></td><td style="color:#94a3b8">另 ' + (s.land.length - 3) + ' 条记录</td></tr>';
    } else {
      h += '<tr><td>批地</td><td style="color:#94a3b8">地政总署卖地 / 换地 / 契约修订库中无记录</td></tr>';
    }
    (s.mtr || []).forEach(function (m) {
      var t = '<b>' + esc(m.name || m.station) + '</b>' +
        (m.station && m.name && m.station !== m.name ? ' <span style="color:#94a3b8">' + esc(m.station) + '</span>' : '');
      if (m.award_ym) t += '<br>招标批出 ' + esc(m.award_ym);
      if (m.gfa) t += (m.award_ym ? ' · ' : '<br>') + '楼面 ' + fmtUnits(m.gfa) + ' ㎡';
      if (m.site_ha) t += (m.award_ym ? ' · ' : '<br>') + '地盘 ' + m.site_ha + ' 公顷';
      if (m.developer) t += '<br><span style="color:#94a3b8">' + esc(m.developer) + '</span>';
      if (m.completion) t += '<br><span style="color:#94a3b8">落成 ' + esc(m.completion) + '</span>';
      t += '<br><span style="color:#94a3b8">港铁不公布招标金额</span>';
      h += '<tr><td>港铁</td><td>' + t + '</td></tr>';
    });
    (s.ura || []).forEach(function (u) {
      var t = '<b>' + esc(u.name) + '</b>' + (u.code ? ' <span style="color:#94a3b8">' + esc(u.code) + '</span>' : '');
      if (u.gfa) t += '<br>总楼面 ' + fmtUnits(u.gfa) + ' ㎡' + (u.gfa_resi ? '（住宅 ' + fmtUnits(u.gfa_resi) + ' ㎡）' : '');
      if (u.units) t += (u.gfa ? ' · ' : '<br>') + '规划 ' + fmtUnits(u.units) + ' 伙';
      var d = u.tender;
      if (d) {
        t += '<br>招标 ' + esc(d.date) + ' · 中标 <b>' + fmtPremium(d.amount_m) + '</b>' +
             (d.joint > 1 ? ' <span style="color:#94a3b8">（' + d.joint + ' 个项目合并招标）</span>' : '');
        if (d.winner) t += '<br><span style="color:#94a3b8">' + esc(d.winner) + (d.parent ? '（' + esc(d.parent) + '）' : '') + '</span>';
        t += bidHtml(d.n_tender, d.underbids_m, d.amount_m, '');
      }
      if (u.programme) t += '<br><span style="color:#94a3b8">' + esc(u.programme) + '</span>';
      h += '<tr><td>市建局</td><td>' + t + '</td></tr>';
    });
    (s.consent_pending || []).forEach(function (c) {
      h += '<tr><td>预售申请</td><td>待批 <span style="color:#94a3b8">（' + esc(c.ym) + ' 地政总署月报，按' + esc(c.match) + '对上）</span>' +
        (c.units ? '<br>申请 <b>' + fmtUnits(c.units) + '</b> 伙' : '') +
        (c.est_completion ? ' · 预计落成 ' + esc(c.est_completion) : '') +
        (c.development && c.development.indexOf('Pending') < 0 ? '<br><span style="color:#94a3b8">' + esc(c.development) + '</span>' : '') +
        '</td></tr>';
    });
    if (s.plan_ym) h += '<tr><td>批则</td><td>' + esc(s.plan_ym) + '</td></tr>';
    if (s.f_build === '未开工') h += '<tr><td>上盖</td><td style="color:#94a3b8">屋宇署未发上盖施工同意书</td></tr>';
    if (s.start_ym) h += '<tr><td>动工</td><td>' + esc(s.start_ym) + (s.bd_units != null ? ' · <b>' + fmtUnits(s.bd_units) + '</b> 伙' : '') +
      (s.bd_sites > 1 ? '（' + s.bd_sites + ' 个屋宇署地盘）' : '') + '</td></tr>';
    else if (s.no_presale) h += '<tr><td>来源</td><td style="color:#94a3b8">现楼销售：没有预售同意书，地政总署 / 屋宇署的链上接不到，落点用 house730 坐标</td></tr>';
    else if (s.stage !== '批地未动工') h += '<tr><td>动工</td><td style="color:#94a3b8">' + (s.bd_missing ? '屋宇署 5.4/5.5 未登记此盘（只有批则 / 入伙纸）' : '屋宇署无施工同意书记录') + '</td></tr>';
    if (s.presale_first) {
      var ph = s.presale_first === s.presale_last ? s.presale_first : s.presale_first + ' ~ ' + s.presale_last;
      h += '<tr><td>预售批出</td><td>' + esc(ph) + ' · <b>' + fmtUnits(s.presale_units) + '</b> 伙';
      if (s.phases && s.phases.length > 1) {
        h += '<details><summary>' + s.phases.length + ' 期明细</summary>';
        s.phases.forEach(function (p) { h += '<div>' + esc(p.ym) + ' · ' + esc(p.name) + ' · ' + fmtUnits(p.units) + ' 伙</div>'; });
        h += '</details>';
      }
      h += '</td></tr>';
    }
    if (s.sale) {
      // 已推出 = house730 收录到单位表的期数；和上面「预售批出」是两个口径，
      // 差额就是批了预售还没开卖的期数，写清楚免得看着像两个互相矛盾的数
      var gap = (s.presale_units || 0) - s.sale.total;
      h += '<tr><td>销售</td><td>' + (s.sale.first_sales ? '开售 ' + esc(s.sale.first_sales) + ' · ' : '') +
        '已推出 <b>' + fmtUnits(s.sale.total) + '</b> 伙 · 已售 <b>' + fmtUnits(s.sale.sold) + '</b> · 余 <b>' + fmtUnits(s.sale.remaining) + '</b>' +
        (gap > 0 ? '<br><span style="color:#94a3b8">另有 ' + fmtUnits(gap) + ' 伙已批预售、未推出</span>' : '') +
        '<br><span style="color:#94a3b8">house730：' + esc(s.sale.projects.join('、')) + '</span></td></tr>';
    }
    if (s.op_ym) h += '<tr><td>入伙</td><td>' + esc(s.op_ym) + ' · <b>' + fmtUnits(s.op_units) + '</b> 伙</td></tr>';
    if ((s.packages || []).length > 1) {
      h += '<tr><td>分期</td><td>共 <b>' + s.packages.length + '</b> 个项目<details><summary>各项目明细</summary>';
      s.packages.forEach(function (p) {
        var left = (p.units != null && p.sold != null && !p.sold_partial) ? Math.max(0, p.units - p.sold) : null;
        h += '<div>' + esc(p.label || p.name) + (p.subs && p.subs.length > 1 ? '（' + p.subs.length + ' 子期）' : '') + ' · ' + (p.units != null ? fmtUnits(p.units) + ' 伙' : '—') +
          (p.sold != null ? ' · 已售 ' + fmtUnits(p.sold) : '') + (left ? ' · 余 ' + fmtUnits(left) : '') +
          (p.active === 'Y' ? '' : ' <span style="color:#94a3b8">(已停售/售罄)</span>') + '</div>';
      });
      h += '</details></td></tr>';
    }
    h += '</table>';
    var who = [];
    if (s.applicant) who.push('申请人 ' + s.applicant.replace(/<br\s*\/?>/g, ' / '));
    if (s.vendor) who.push('卖方 ' + s.vendor);
    if (s.ap) who.push('认可人士 ' + s.ap);
    if (s.address) who.unshift(s.address);
    if (who.length) h += '<div class="addr">' + esc(who.join(' · ')) + '</div>';
    return h;
  }

  var sizeBy = 'units', splitOn = true, bindGroups = function () {};
  function sizeValue(s) {
    if (sizeBy === 'remaining') return s.sale ? s.sale.remaining : 0;
    return s.presale_units || s.bd_units || 0;
  }
  function radius(s) {
    var u = sizeValue(s);
    if (!u && s.f_build === '未开工' && s.area) return Math.max(4, Math.min(20, Math.sqrt(s.area) / 9));   // 没伙数，按地盘面积
    if (!u) return sizeBy === 'remaining' ? 3 : 5;
    return Math.max(4, Math.min(24, Math.sqrt(u) / (sizeBy === 'remaining' ? 1.3 : 2.2)));
  }

  function currentFilter() {
    var f = { min: +document.getElementById('map-min').value,
              q: document.getElementById('map-q').value.trim().toLowerCase() };
    sec.querySelectorAll('.fgrp[data-dim]').forEach(function (g) {
      var on = {};
      g.querySelectorAll('input[value]').forEach(function (c) { if (c.checked) on[c.value] = 1; });
      f[g.getAttribute('data-dim')] = on;
    });
    return f;
  }

  // 组里「全部」和各项联动：勾全部 = 全勾，取消任一项就把全部去掉
  function syncGroup(g, fromAll) {
    var all = g.querySelector('input[data-all]');
    var items = g.querySelectorAll('input[value]');
    if (fromAll) {
      items.forEach(function (c) { c.checked = all.checked; });
    } else {
      all.checked = Array.prototype.every.call(items, function (c) { return c.checked; });
    }
  }

  // 一块地分多期卖的盘（港铁上盖、NOVO LAND…），预售同意书共用一个地段号，必然并成一个地盘。
  // 一手销售资讯网按「发展项目」逐期登记，每期有自己的坐标和成交纪录册，就用它拆。
  function expand(s) {
    var ps = s.packages || [];
    if (!splitOn || ps.length < 2) return [s];
    return ps.map(function (p) {
      // 成交册早于《一手住宅物業銷售條例》生效的期，册子只记了条例后的成交，
      // 「批出伙数 - 册子成交数」不是余货，不据此算
      var left = (p.units != null && p.sold != null && !p.sold_partial)
        ? Math.max(0, p.units - p.sold) : null;
      var selling = p.active === 'Y' && !p.sold_partial && (left == null || left > 0);
      return Object.assign({}, s, {
        lat: p.lat, lon: p.lon, _proj: p, _parent: s,
        name: (p.name || s.name) + (p.label ? ' ' + p.label : ''),
        presale_units: p.units || 0,
        f_sale: selling ? '在售' : (p.units == null ? s.f_sale : '售罄'),
        sale: (p.units != null && p.sold != null)
          ? { projects: [], total: p.units, sold: p.sold, remaining: left, first_sales: p.first_print } : null
      });
    });
  }

  function render() {
    if (!map || !data) return;
    var f = currentFilter();
    layer.clearLayers();
    var shown = [], remaining = 0, srcCnt = {}, saleCnt = {}, saleUnits = {}, premium = 0, nPremium = 0;
    var list = [];
    data.sites.forEach(function (s) { expand(s).forEach(function (x) { list.push(x); }); });
    list.forEach(function (s) {
      if (!f.src[s.source] || !f.presale[s.f_presale] || !f.sale[s.f_sale] || !f.build[s.f_build]) return;
      var u = s.presale_units || s.bd_units || 0;
      if (f.min && u < f.min) return;
      if (f.q) {
        var hay = (s.name + ' ' + s.name_en + ' ' + s.address + ' ' + (s.land || []).map(function (r) { return r.lot; }).join(' ') + ' ' + (s.ura || []).map(function (u) { return u.name + ' ' + u.name_en + ' ' + u.code; }).join(' ') + ' ' + (s.mtr || []).map(function (m) { return m.name + ' ' + m.station + ' ' + m.developer; }).join(' ')).toLowerCase();
        if (hay.indexOf(f.q) < 0) return;
      }
      shown.push(s);
      saleCnt[s.f_sale] = (saleCnt[s.f_sale] || 0) + 1;
      saleUnits[s.f_sale] = (saleUnits[s.f_sale] || 0) + u;
      if (s.sale) remaining += s.sale.remaining;
      if (s.premium_m) { premium += s.premium_m; nPremium++; }
      srcCnt[s.source] = (srcCnt[s.source] || 0) + 1;
    });
    drawLegend(srcCnt);
    // 大的先画在下面，小的在上面，免得被盖住点不到
    shown.sort(function (a, b) { return radius(b) - radius(a); });
    shown.forEach(function (s) {
      var st = styleOf(s);
      var m = L.circleMarker([s.lat, s.lon], {
        radius: radius(s), color: st.color || SRC_COLOR[s.source] || '#999', fillColor: SRC_COLOR[s.source] || '#999',
        fillOpacity: st.fillOpacity, weight: st.weight, dashArray: st.dashArray, opacity: 0.95
      });
      m.bindPopup(function () { return popupHtml(s); }, { maxWidth: 340 });
      var tip = s.name + (s.presale_units || s.bd_units ? ' · ' + fmtUnits(s.presale_units || s.bd_units) + ' 伙' : '');
      if (s.sale && s.sale.remaining > 0) tip += ' · 余 ' + fmtUnits(s.sale.remaining);
      m.bindTooltip(tip, { direction: 'top', offset: [0, -4] });
      layer.addLayer(m);
    });
    var parts = [];
    DIMS.sale.forEach(function (k) {
      if (!saleCnt[k]) return;
      parts.push(k + ' ' + saleCnt[k] + ' 个' + (saleUnits[k] ? '（' + fmtUnits(saleUnits[k]) + ' 伙）' : ''));
    });
    note('显示 ' + shown.length + (splitOn ? ' 个（含分期）：' : ' 个地盘：') + (parts.join(' · ') || '无') +
      (remaining ? ' · 余货合计 ' + fmtUnits(remaining) + ' 伙' : '') +
      (nPremium ? ' · 其中 ' + nPremium + ' 幅有地价，合计 ' + (premium / 100).toFixed(0) + ' 亿' : '') +
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
    legend.onAdd = function () { legendEl = L.DomUtil.create('div', 'map-legend'); return legendEl; };
    legend.addTo(map);

    fetch('land_chain.json', { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) {
        data = j;
        var g = sec.querySelector('.fgrp[data-dim="src"]');
        DIMS.src = SRC_ORDER.filter(function (k) {
          return data.sites.some(function (s) { return s.source === k; });
        });
        DIMS.src.forEach(function (k) {
          var l = document.createElement('label');
          l.innerHTML = '<input type="checkbox" value="' + k + '" checked> ' + k;
          g.appendChild(l);
        });
        bindGroups();
        render();
      })
      .catch(function (e) { note('地图数据 land_chain.json 加载失败：' + e.message); });

    var sizeSel = document.getElementById('map-size');
    sizeSel.addEventListener('change', function () { sizeBy = this.value; render(); });
    bindGroups = function () {
      sec.querySelectorAll('.fgrp[data-dim]').forEach(function (g) {
        if (g.dataset.bound) return;
        g.dataset.bound = '1';
        g.addEventListener('change', function (e) {
          syncGroup(g, e.target.hasAttribute('data-all'));
          // 只看在售时，圆点按余货算才有意义
          var sale = g.parentNode.querySelector('.fgrp[data-dim="sale"]');
          var on = Array.prototype.filter.call(sale.querySelectorAll('input[value]'), function (c) { return c.checked; });
          sizeBy = (on.length === 1 && on[0].value === '在售') ? 'remaining' : 'units';
          sizeSel.value = sizeBy;
          render();
        });
        syncGroup(g, false);
      });
    };
    bindGroups();
    document.getElementById('map-min').addEventListener('change', render);
    document.getElementById('map-split').addEventListener('change', function () { splitOn = this.checked; render(); });
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
