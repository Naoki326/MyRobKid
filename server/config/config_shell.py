"""config_shell.py — 配置页共享外壳（侧栏 + 顶栏），服务端模板。

父 spec §4.2 拍板：**服务端共享模板**——一个 Python 模块生成侧栏 + 顶栏 HTML，
各页 handler 调用注入正文。理由（原文）：现状无模板引擎、无静态目录，共享模板
是单一事实源、最小改动、无客户端闪烁。JS 注入被否（首屏空壳闪烁）；各页自带
外壳被否（漂移）。

本模块因此是三条约束的唯一落点：

1. **五域 + 逃生口**（§3）：侧栏顶部五域，底部横线之下的**非域小入口**通往
   只读原始配置页；逃生口不是侧栏第六域。未上线域（engine / tools / devices）
   条目**可见但标注未上线**，不删也不装成已上线。
2. **动作区只在编辑页渲染**（§4.2）：保存 / 重启服务留在域编辑页；只读页
   （逃生口）与占位页拿不到按钮——它们的顶栏动作区是空的，这是渲染期决定，
   不是运行时隐藏。
3. **尾斜杠规范形 + 应用层 301**（§8.2）：每个页面恰一个规范 URL，见 ``PAGES``。

壳的客户端脚本（``SHELL_JS``）只做两件不做就不成立的事：侧栏脏点（§4.6 跨页
脏状态）与未保存离开确认（同节 beforeunload 兜底）。域页正文与编辑逻辑不在
这里——它们在 config_domain_page.html。
"""

#: 五个页面域 + 逃生口（父 spec §8.1 定案的 slug）。
#:
#: ``implemented`` 为 False 的域本票只交付**占位页**（#29 才实现内容）；
#: ``raw`` 是逃生口，不出现在五域序列里，单独挂在侧栏底部横线之下。
DOMAINS = [
    {"slug": "dialogue", "label": "对话与角色", "icon": "🎭", "implemented": True,
     "blurb": "提示词系、唤醒/退出词、声纹身份、上下文源"},
    {"slug": "engine", "label": "引擎", "icon": "🤖", "implemented": True,
     "blurb": "VAD/ASR/LLM/VLLM/TTS/Memory 六族 + 选择器 + 引擎全局参数"},
    {"slug": "tools", "label": "插件与工具", "icon": "🧩", "implemented": True,
     "blurb": "Intent 子树、plugins.*、外部 MCP、工具调用参数"},
    {"slug": "devices", "label": "设备", "icon": "📟", "implemented": True,
     "blurb": "下发给设备的连接载荷、设备认证、hello 协商、摄像头入口"},
    {"slug": "system", "label": "系统", "icon": "⚙️", "implemented": True,
     "blurb": "监听地址、会话/文件生命周期、传输保活、日志、跨用途路径"},
]

#: 逃生口：侧栏底部横线之下的非域小入口（§4.3）。
RAW_ESCAPE = {"slug": "raw", "label": "原始配置", "icon": "📦"}

#: 摄像头页（§4.4 / §8.1）——**保留独立 URL** 作书签挂机监控，归设备域的
#: 实时视图，**不占一级导航**（侧栏五域不变）。
#:
#: 这里是它的**单一事实源**：路径（§8.1 定的规范形）、顶栏家族互链的文案与
#: 图标都从这一处取。设备域里的「常驻入口」也指向它——
#: 入口不存在线设备列表里（那是可选增强）：设备离线时入口仍在。
CAMERA_PAGE = {
    "slug": "camera",
    "path": "/xiaozhi/camera/",
    "label": "摄像头实时画面",
    "icon": "📷",
}

#: 旧的五组八页配置页（§1 现状）。父 spec §11 的 expand 阶段：**旧页面原样保留
#: 在原 URL**，收线（302 + 退役）是后续票的事。这里只登记它们的 URL，供 301 的
#: 规范形指向用；本模块不渲染它们。
LEGACY_CONFIG_URL = "/xiaozhi/config/"

#: 规范形 URL 前缀。每个页面 URL 以尾斜杠收尾（§8.2）。
def page_url(slug: str) -> str:
    """任意域/逃生口页的规范 URL（尾斜杠形式）。"""
    return f"/xiaozhi/config/{slug}/"


#: 壳的样式（CSS 变量即壳 tokens，§4.2）。摄像头页局部覆盖为暗色，各页不重定义
#: 骨架 —— 所以壳样式单独一段，由页面骨架（config_domain_page.html）里的
#: ``__SHELL_CSS__`` 占位符注入所有页面。
SHELL_CSS = """
:root{
  --bg:#0b0f1a; --bg2:#111827; --panel:#141c2e; --panel2:#1a2338; --line:#243048;
  --text:#e6edf7; --dim:#8b98b3; --accent:#5b8cff; --accent2:#7c5cff;
  --ok:#34d399; --warn:#fbbf24; --err:#f87171;
  --grad:linear-gradient(135deg,#5b8cff,#7c5cff);
  --sidebar-w:220px; --topbar-h:60px;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font:14px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh;overflow-x:hidden}

/* ---- 顶栏 ---- */
.topbar{position:fixed;top:0;left:0;right:0;height:var(--topbar-h);z-index:100;background:rgba(11,15,26,.85);backdrop-filter:blur(16px);border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 24px;gap:16px}
.logo{display:flex;align-items:center;gap:10px;font-size:17px;font-weight:700}
.logo .orb{width:28px;height:28px;border-radius:50%;background:var(--grad);box-shadow:0 0 16px rgba(91,140,255,.5);display:flex;align-items:center;justify-content:center;font-size:14px}
.topbar .right{margin-left:auto;display:flex;align-items:center;gap:12px}
.btn{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:10px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s;display:inline-flex;align-items:center;gap:6px}
.btn:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.3);border-color:var(--accent)}
.btn.primary{background:var(--grad);color:#fff;border:none}
.btn.primary:hover{box-shadow:0 4px 16px rgba(91,140,255,.4)}
.btn.danger{background:linear-gradient(135deg,#f87171,#ef4444);color:#fff;border:none}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none}

/* ---- 布局 ---- */
.layout{display:flex;padding-top:var(--topbar-h);min-height:100vh}
.sidebar{width:var(--sidebar-w);flex-shrink:0;position:fixed;top:var(--topbar-h);bottom:0;left:0;background:var(--bg2);border-right:1px solid var(--line);padding:20px 12px;overflow-y:auto;display:flex;flex-direction:column}
.navlist{display:flex;flex-direction:column}
.navitem{display:flex;align-items:center;gap:10px;padding:11px 14px;border-radius:10px;cursor:pointer;color:var(--dim);font-size:13px;font-weight:500;margin-bottom:2px;transition:all .2s;position:relative;text-decoration:none}
.navitem:hover{color:var(--text);background:rgba(91,140,255,.08)}
.navitem.active{color:#fff;background:rgba(91,140,255,.15)}
.navitem.active::before{content:'';position:absolute;left:-12px;top:50%;transform:translateY(-50%);width:4px;height:20px;border-radius:0 4px 4px 0;background:var(--grad)}
.navitem .ic{font-size:15px;width:20px;text-align:center}
.navitem .lbl{flex:1}
.navitem.pending{cursor:default;opacity:.75}
.navitem.pending:hover{background:transparent;color:var(--dim)}
/* 未上线域占位标注（§11 Phase 1：域 slug 可达，内容是 #27/#28/#29） */
.navitem .pending-tag{font-size:10px;color:var(--dim);border:1px solid var(--line);border-radius:8px;padding:0 6px;white-space:nowrap}
/* 跨页脏点（§4.6）：侧栏域项挂未保存标记，计数来自 localStorage 摘要 */
.navitem .dirty-dot{width:8px;height:8px;border-radius:50%;background:var(--warn);box-shadow:0 0 8px var(--warn);flex-shrink:0}
.navitem .dirty-dot[hidden]{display:none}
/* 逃生口：横线之下的非域样式小入口（§3/§4.3），明显比域项轻 */
.escape-sep{border:none;border-top:1px solid var(--line);margin:14px 4px 10px}
.escape{margin-top:auto}
.navitem.escape-item{font-size:12px;padding:8px 14px;color:var(--dim)}
.navitem.escape-item .ic{font-size:13px}
/* 顶栏的家族互链（§4.4）：设备页 ↔ 摄像头页。不是一级导航——一级导航在侧栏。 */
.topnav{display:flex;align-items:center;gap:6px}
.topnav a{display:inline-flex;align-items:center;gap:6px;color:var(--dim);text-decoration:none;font-size:12.5px;font-weight:500;padding:6px 12px;border-radius:9px;border:1px solid transparent;transition:all .2s}
.topnav a:hover{color:var(--text);background:rgba(91,140,255,.08)}
.topnav a.on{color:#fff;background:rgba(91,140,255,.15);border-color:rgba(91,140,255,.35)}
.content{flex:1;margin-left:var(--sidebar-w);padding:28px 32px 120px;max-width:980px}

/* ---- 分组卡片 ---- */
.group{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:24px;margin-bottom:20px;box-shadow:0 4px 24px rgba(0,0,0,.2);position:relative;overflow:hidden}
.group h2{font-size:15px;font-weight:600;margin-bottom:6px;display:flex;align-items:center;gap:8px}
.group h2 .badge{font-size:11px;background:rgba(91,140,255,.15);color:var(--accent);padding:2px 10px;border-radius:12px;font-weight:500}
.group .desc{color:var(--dim);font-size:12px;margin-bottom:18px;line-height:1.5}
.pagehead{margin-bottom:22px}
.pagehead h1{font-size:20px;font-weight:700}
.pagehead .desc{color:var(--dim);font-size:12.5px;margin-top:6px}
.placeholder{color:var(--dim);font-size:13px;line-height:1.8}

/* ---- 表单项 ---- */
.row{display:flex;align-items:flex-start;gap:16px;padding:14px 0;border-bottom:1px solid rgba(36,48,72,.5)}
.row:last-child{border-bottom:0}
.row .meta{width:200px;flex-shrink:0;padding-top:4px}
.row .meta .label{font-size:13px;font-weight:500}
.row .meta .hint{color:var(--dim);font-size:11px;margin-top:2px;line-height:1.4}
.row .ctrl{flex:1;min-width:0}
input[type=text],input[type=password],input[type=number],select,textarea{width:100%;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 14px;color:var(--text);font-size:13px;font-family:inherit;transition:all .2s}
input:hover,select:hover,textarea:hover{border-color:#2d3a55}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(91,140,255,.15)}
textarea{resize:vertical;min-height:80px;font-family:ui-monospace,Menlo,monospace;line-height:1.6}
select{cursor:pointer}
select option{background:var(--panel2)}
.switch{position:relative;display:inline-block;width:44px;height:24px;cursor:pointer}
.switch input{opacity:0;width:0;height:0}
.switch .sl{position:absolute;inset:0;background:var(--panel2);border:1px solid var(--line);border-radius:24px;transition:.25s}
.switch .sl::before{content:'';position:absolute;width:18px;height:18px;left:2px;top:2px;background:var(--dim);border-radius:50%;transition:.25s}
.switch input:checked+.sl{background:rgba(52,211,153,.25);border-color:var(--ok)}
.switch input:checked+.sl::before{transform:translateX(20px);background:var(--ok)}
.switch-row{display:flex;align-items:center;gap:12px}
.keylist .krow{display:flex;gap:8px;margin-bottom:8px;align-items:center}
.keylist input{flex:1}
.keylist .del{background:transparent;color:var(--err);border:1px solid rgba(248,113,113,.3);padding:6px 12px;border-radius:8px;cursor:pointer;font-size:12px}
.keylist .del:hover{background:rgba(248,113,113,.15)}
.addbtn{background:transparent;color:var(--accent);border:1px dashed rgba(91,140,255,.4);border-radius:10px;padding:8px 16px;cursor:pointer;font-size:12px;font-weight:500;width:100%}
.addbtn:hover{background:rgba(91,140,255,.1);border-style:solid}
details{border:1px solid var(--line);border-radius:10px;margin-top:10px;overflow:hidden}
details summary{cursor:pointer;color:var(--dim);font-size:12px;padding:10px 14px;background:var(--panel2);list-style:none;display:flex;align-items:center;gap:8px}
details summary:hover{color:var(--text)}
details summary::before{content:'▸';font-size:10px;transition:transform .2s}
details[open] summary::before{transform:rotate(90deg)}
details>div{padding:12px 14px}

/* ---- 保存栏 + 脏明细（只在编辑页渲染） ---- */
.savebar{position:fixed;bottom:0;left:var(--sidebar-w);right:0;background:rgba(11,15,26,.9);backdrop-filter:blur(16px);border-top:1px solid var(--line);padding:14px 32px;display:flex;gap:12px;align-items:center;z-index:50}
.savebar .info{font-size:12px;color:var(--dim)}
#dirtyPanel{position:fixed;bottom:64px;left:var(--sidebar-w);right:0;padding:0 32px 8px;font-size:12px;z-index:49;pointer-events:none}
#dirtyPanel b{color:var(--text)}
#dirtyPanel .dirty-item{color:var(--dim);font-size:12px}

/* ---- 只读原始配置 ---- */
.jsonbox{background:#0a0e18;border:1px solid var(--line);border-radius:12px;padding:16px;font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#7dd3fc;white-space:pre-wrap;word-break:break-all;line-height:1.6}

/* ---- toast ---- */
#toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(80px);background:#1a2338;border:1px solid var(--line);color:#fff;padding:12px 22px;border-radius:12px;font-size:13px;font-weight:500;opacity:0;transition:all .3s cubic-bezier(.34,1.56,.64,1);z-index:99;box-shadow:0 8px 32px rgba(0,0,0,.4)}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
#toast.ok{border-color:rgba(52,211,153,.4);color:var(--ok)}
#toast.err{border-color:rgba(248,113,113,.4);color:var(--err)}

@media(max-width:900px){
  :root{--sidebar-w:64px}
  .sidebar{padding:16px 8px}
  .navitem{padding:12px;justify-content:center}
  .navitem .lbl,.navitem .pending-tag{display:none}
  .content{margin-left:var(--sidebar-w);padding:20px 16px 120px}
  .savebar{left:var(--sidebar-w)}
  .row{flex-direction:column;gap:8px}
  .row .meta{width:100%}
  .topbar{padding:0 16px}
}
"""


def render_sidebar(active: str = "") -> str:
    """侧栏：顶部五域、底部横线之下的非域逃生口入口。

    ``active`` 是当前页 slug（未上线的域传自己的 slug 也好使——占位页同样
    高亮自己所在的域，否则用户点进去会以为没生效）。
    """
    items = []
    for d in DOMAINS:
        cls = "navitem"
        if d["slug"] == active:
            cls += " active"
        pending = "" if d["implemented"] else '<span class="pending-tag">未上线</span>'
        # 未上线域的条目仍然指向自己的**规范 URL**（路由可达，内容是占位页），
        # 不做成禁用按钮：slug 是用户契约，先上线地址再上线内容（ADR-0012）。
        items.append(
            f'<a class="{cls}" data-domain="{d["slug"]}" href="{page_url(d["slug"])}">'
            f'<span class="ic">{d["icon"]}</span><span class="lbl">{d["label"]}</span>'
            f'{pending}<span class="dirty-dot" data-dirty-domain="{d["slug"]}" hidden></span>'
            f"</a>"
        )
    raw_cls = "navitem escape-item" + (
        " active" if RAW_ESCAPE["slug"] == active else "")
    return (
        '<div class="navlist">' + "".join(items) + "</div>"
        + '<div class="escape">'
        + '<hr class="escape-sep">'
        + f'<a class="{raw_cls}" data-domain="raw" '
          f'href="{page_url(RAW_ESCAPE["slug"])}">'
          f'<span class="ic">{RAW_ESCAPE["icon"]}</span>'
          f'<span class="lbl">{RAW_ESCAPE["label"]}</span></a>'
        + "</div>"
    )


def render_topnav(active: str = "") -> str:
    """顶栏的**家族互链**（§4.4）：设备页 ↔ 摄像头页，两个方向。

    为什么在顶栏而不是侧栏：摄像头页**不占一级导航**（§4.4 明文）——侧栏
    五域的顺序与数量是骨架级约定。它属于「看设备」家族，所以在**这两页**
    的顶栏上互相给对方一个入口（别的页面上不出现：它们不在这个家族里）。

    ``active`` 是当前所在页的 slug（``devices`` / ``camera``）。两页都渲染
    两条链（而不是「只渲染另一条」）：同壳互链的意思是「这两页是一家的」，
    只给对面那一条会在语义上把当前页排除在家族外。
    """
    devices = next(d for d in DOMAINS if d["slug"] == "devices")
    items = [
        (devices["slug"], page_url(devices["slug"]), devices["icon"],
         devices["label"]),
        (CAMERA_PAGE["slug"], CAMERA_PAGE["path"], CAMERA_PAGE["icon"],
         CAMERA_PAGE["label"]),
    ]
    links = "".join(
        f'<a class="{"on" if slug == active else ""}" data-family-link="{slug}" '
        f'href="{href}"><span>{icon}</span>{label}</a>'
        for slug, href, icon, label in items
    )
    return f'<nav class="topnav">{links}</nav>'


def render_topbar(title: str, actions: str = "", nav: str = "") -> str:
    """顶栏。

    ``actions`` 由页面自己给：**只有配置编辑页有动作区**（保存 / 重启服务）。
    只读页（逃生口）与未上线域的占位页传空串——动作区在那类页面上**不渲染**，
    不是渲染了再藏（§4.2 的验收措辞是「在编辑页之外不渲染」）。

    ``nav`` 同理由页面自己给：**只有设备家族的两页**（设备域 / 摄像头页）
    渲染家族互链（§4.4）。
    """
    return (
        '<div class="topbar">'
        f'<div class="logo"><div class="orb">🤖</div><span>{title}</span></div>'
        f'{nav}'
        f'<div class="right">{actions}</div>'
        "</div>"
    )


def render_placeholder(domain: dict) -> str:
    """未上线域的占位页正文（本票只交付占位，#27/#28/#29 交付内容）。

    占位页必须**如实说自己未上线**，并把该域负责什么写清楚（照抄 §3 的职责
    一句话），否则用户看到空页会以为配置丢了。页面标题已由域页骨架渲染，
    这里不再重复一遍。
    """
    return (
        '<section class="group"><h2>该域尚未上线 <span class="badge">占位</span></h2>'
        '<div class="placeholder">'
        "这个页面域已经在侧栏与路由上就位（URL 一经上线即用户契约），"
        "但内容还没有实现——它的字段目前仍可以在旧的分组配置页与"
        "「原始配置」只读页里查看。<br><br>"
        f"<b>计划归属本域的配置</b>：{domain['blurb']}。<br><br>"
        "在它上线之前，改动这些字段请走旧的分组配置页（"
        f'<a href="{LEGACY_CONFIG_URL}">{LEGACY_CONFIG_URL}</a>）；'
        "只想核对现值时，用侧栏底部的「原始配置」页——那里有完整的原始 YAML。"
        "</div></section>"
    )


#: 壳脚本：跨页脏状态（§4.6）。只做两件事，两件都是「不做就不成立」的那种。
#:
#: 1. **侧栏脏点**：读各域写在 localStorage 的**脏摘要**（只含路径与计数，不含
#:    任何值）画点。摘要是页面写给侧栏看的，侧栏不重新算——域页自己才知道哪些
#:    字段脏、密钥新值更没有理由离开页面内存。
#: 2. **beforeunload 兜底**：带脏离开/刷新/关页先确认。域页把「本页有脏」写到
#:    全局钩子上（``window.__xzhDirtyGuard``）；壳只负责把钩子接到浏览器事件。
#:
#: 为什么脏摘要在 localStorage 而不是服务端：父 spec §4.6 拍板——编辑缓冲只活
#: 在当前页内存，密钥值永不落地。localStorage 里只有 ``{count, paths}``。
SHELL_JS = """
(() => {
  // ---- 跨页脏摘要的存储协议（与 config_domain_page.js 共用） ----
  // 键：config-dirty/<域 slug>；值：{count, paths:[...]} —— 只有路径与计数。
  const KEY = (domain) => 'config-dirty/' + domain;
  const readSummary = (domain) => {
    try {
      const raw = localStorage.getItem(KEY(domain));
      if (!raw) return null;
      const s = JSON.parse(raw);
      if (!s || typeof s !== 'object') return null;
      const count = Number(s.count) || 0;
      if (count <= 0) return null;
      return { count, paths: Array.isArray(s.paths) ? s.paths : [] };
    } catch (e) { return null; }
  };

  // 侧栏脏点：每个域项一个点，域数 = 五域 + 逃生口（逃生口只读，永远不脏）。
  const paint = () => {
    document.querySelectorAll('[data-dirty-domain]').forEach(dot => {
      const summary = readSummary(dot.dataset.dirtyDomain);
      dot.hidden = !summary;
      const item = dot.closest('.navitem');
      if (item) item.title = summary ? (`本域 ${summary.count} 处未保存修改`) : '';
    });
  };
  // 域页写好摘要后要通知壳重画。两条路都给：
  //   1. 自定义事件（壳先就位时用）——正常加载顺序下走这条；
  //   2. window 上的函数（域页先就位时用）——模块脚本与 classic 脚本的执行
  //      顺序随加载方式而变，靠“先来后到”约定不如两边都认。
  window.__xzhPaintDirtyDots = paint;
  window.addEventListener('xzh:dirty-changed', paint);

  // 别的标签页保存/放弃后，本页的脏点也要跟着变（storage 事件只在别的标签页触发）。
  window.addEventListener('storage', paint);
  window.addEventListener('pageshow', paint);
  paint();

  // ---- 带脏切换域页先确认 + 刷新/关页 beforeunload 兜底（§4.6） ----
  // 域页把自己的脏计数挂到 window.__xzhDirtyGuard；壳不猜，只读。
  const domainDirtyCount = () => {
    const g = window.__xzhDirtyGuard;
    return g && typeof g.count === 'function' ? g.count() : 0;
  };
  const confirmText = () => {
    const g = window.__xzhDirtyGuard;
    const paths = g && typeof g.paths === 'function' ? g.paths() : [];
    const head = '本页有 ' + domainDirtyCount() + ' 处未保存修改。';
    const list = paths.length ? ('\\n· ' + paths.join('\\n· ')) : '';
    return head + list + '\\n\\n离开将丢弃这些修改。';
  };

  document.addEventListener('click', (ev) => {
    const link = ev.target.closest('a[data-domain]');
    if (!link) return;
    // 同域内部跳（含 hash）不算离开，不打扰。
    const here = window.location.pathname;
    if (link.getAttribute('href') === here) return;
    if (domainDirtyCount() > 0 && !window.confirm(confirmText())) {
      ev.preventDefault();
    }
  });

  window.addEventListener('beforeunload', (ev) => {
    if (domainDirtyCount() > 0) {
      ev.preventDefault();
      ev.returnValue = confirmText();
      return ev.returnValue;
    }
  });
})();
"""

