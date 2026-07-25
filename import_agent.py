"""智能导入助手：带工具调用、多步推理的作业导入 Agent。

红线（与交接文档一致）：
- Agent 只有"读"类工具 + 一个 save_draft 口子，绝不直接写业务数据。
- 所有产出进"待导入草稿"，老师在界面上确认后才落库为待确认错题。
- AI 不识别照片里是谁、不改变作业归属、不凭空生成结论。

本模块不 import server；由 server.py 构造 Ctx 注入依赖（数据访问、模型调用、图片读取），
保持零循环引用，也方便单独测试。
"""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import ipaddress
import json
import re
import socket
import time

MAX_STEPS = 12          # 单个会话一次运行的最大模型决策步数
STEP_TIMEOUT = 120      # 单次模型调用超时（秒）
FETCH_TIMEOUT = 20      # 抓网页/搜索超时（秒）
MAX_FETCH_BYTES = 400_000   # fetch_url 最多读取的原始字节
MAX_TEXT_CHARS = 6000   # 喂回模型的网页/Excel/PDF 文本上限
MAX_SEARCH_RESULTS = 5
MAX_EXCEL_ROWS = 200
MAX_EXCEL_COLS = 30
MAX_PDF_PAGES = 20

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# ---------------------------------------------------------------- 工具 schema（OpenAI 兼容）

def _schema(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)}}}

TOOLS = [
    _schema("query_students", "查询当前老师可见的班级与学生名单。Excel 成绩单、试卷姓名与系统名单对齐前必须先调用。",
            {}),
    _schema("list_lessons", "列出当前老师可见的课次（日期、节次、班级、课程名、课次 id）。草稿必须归属到某节课，保存前用它与老师确认目标课次。",
            {}),
    _schema("get_lesson_context", "获取本次导入目标课次的信息：日期节次、课程标题、知识点、学生名单。",
            {}),
    _schema("read_image", "查看一张已上传的图片（作业本照片、试卷照片、网页截图）。返回图片内容供你直接看图。",
            {"path": {"type": "string", "description": "uploads/ 开头的图片路径"}}, ["path"]),
    _schema("parse_excel", "用代码解析已上传的 Excel/CSV 成绩单为结构化表格（不耗费你的识别能力）。返回每个工作表的行列数据。",
            {"path": {"type": "string", "description": "uploads/ 开头的 .xlsx/.xls/.csv 路径"}}, ["path"]),
    _schema("parse_pdf", "用代码提取已上传电子试卷 PDF 的文字内容（用于建立题目清单）。扫描件无文字时会提示改用 read_image。",
            {"path": {"type": "string", "description": "uploads/ 开头的 .pdf 路径"}}, ["path"]),
    _schema("fetch_url", "抓取指定网页的正文内容（在线作业平台页面、题目页等）。需要登录的页面抓不到内容。",
            {"url": {"type": "string", "description": "http/https 链接"}}, ["url"]),
    _schema("web_search", "联网搜索公开信息（如根据题号查题目出处与知识点）。结果来自免费搜索引擎，可能为空。",
            {"query": {"type": "string", "description": "搜索关键词"}}, ["query"]),
    _schema("save_draft", "把整理好的导入结果保存为【待导入草稿】。这是唯一的写入口：草稿由老师确认后才会进入系统。"
            "每个学生一个 rows 元素；无法对齐名单的学生也要保留并标 unmatched。",
            {"title": {"type": "string", "description": "草稿标题，如「2026春季月考成绩导入」"},
             "lessonId": {"type": "string", "description": "目标课次 id（先用 list_lessons 与老师确认）；会话已绑定课次时可省略"},
             "summary": {"type": "string", "description": "整体说明：来源、共几人几题、需要老师注意什么"},
             "rows": {"type": "array", "items": {"type": "object", "properties": {
                 "studentName": {"type": "string"},
                 "question": {"type": "string", "description": "题号与简短题目"},
                 "knowledgePoint": {"type": "string"},
                 "errorType": {"type": "string", "description": "知识点不理解/计算错误/审题错误/步骤不完整/答案表达错误/未作答/其他/待老师确认"},
                 "score": {"type": "string", "description": "该题得分/失分说明，可空"},
                 "confidence": {"type": "number", "description": "0到1"},
                 "note": {"type": "string", "description": "判断依据或需老师确认的原因"}},
                 "required": ["studentName", "question"]}}},
            ["title", "rows"]),
]

SOUL = """你是学生管理系统里的「智能导入助手」，帮老师把各种来源的作业数据整理成可导入的结构化草稿。

工作方式：
- 你可以多次调用工具逐步完成任务；先看老师给了什么材料，再决定调用哪个工具。
- Excel/CSV 一律用 parse_excel 解析；PDF 一律用 parse_pdf 提取；图片用 read_image 查看；网页用 fetch_url 抓取；公开题目信息可用 web_search 补充。
- 涉及学生姓名时，先 query_students 拿系统名单，把材料里的姓名对齐到名单；对不上（错别字、昵称、缺人）时在该行的 note 里写「未匹配」，绝不能自己发明学生。
- 涉及课次信息时用 get_lesson_context。

红线（必须遵守）：
1. 你只能产出「待导入草稿」（save_draft），没有任何其他写入能力；不要声称自己已经导入了系统。
2. 不识别照片里的学生是谁——作业归属只依据老师指定的学生或成绩单里的姓名列。
3. 不编造：图片模糊、表格列含义不清、网页抓不到内容时，如实说明并把对应行 confidence 调低、note 写明原因。
4. 每完成一步，用一两句话告诉老师你做了什么、发现了什么。
5. 草稿必须归属到一节课。会话没有预定课次时，先 list_lessons 列出课次，根据材料里的日期/课程信息向老师确认「导入到哪节课」，确认后再 save_draft 并带上 lessonId。

产出习惯：
- 月考试卷场景：先 parse_pdf/看试卷图建立题目清单（题号→知识点），再 parse_excel 读得分矩阵，交叉得到每人失分题，最后 save_draft。
- 老师只发来材料没说目标时，主动问一句导入到哪节课、材料是什么考试/作业。
- 全部完成后，用中文简要汇报：草稿标题、行数、归属课次、未匹配姓名清单、需要老师确认的点。"""


# ---------------------------------------------------------------- 运行时

class Ctx:
    """server.py 注入的依赖集合。"""
    def __init__(self, *, user, lesson, chat, image_message, data_snapshot, uploads_root, emit):
        self.user = user                # 当前登录用户（dict）
        self.lesson = lesson            # 目标课次（dict，可能为 None）
        self.chat = chat                # chat(messages, tools) -> assistant message dict
        self.image_message = image_message  # path -> OpenAI content part（image_url）
        self.data_snapshot = data_snapshot  # () -> filtered_data(user) 结构
        self.uploads_root = Path(uploads_root)
        self.emit = emit                # emit(kind, text, **extra)


class DraftError(ValueError):
    pass


def run_agent(ctx, messages, drafts):
    """多步工具循环。直接修改传入的 messages / drafts 列表。返回最终文本回复。"""
    for step in range(MAX_STEPS):
        ctx.emit("status", f"正在思考（第 {step + 1} 步）…")
        reply = ctx.chat(messages, TOOLS)
        messages.append(reply)
        calls = reply.get("tool_calls") or []
        if not calls:
            ctx.emit("status", "本轮完成")
            return reply.get("content") or "（没有更多说明）"
        for call in calls:
            fn = (call.get("function") or {})
            name, call_id = fn.get("name", ""), call.get("id", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            ctx.emit("tool", _tool_brief(name, args), tool=name)
            try:
                result, follow_image = _dispatch(ctx, name, args, drafts)
            except Exception as exc:  # 工具失败不中断会话，把错误喂回模型让它换路
                result, follow_image = f"工具执行失败：{exc}", None
                ctx.emit("tool", f"{name} 失败：{exc}", tool=name)
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
            if follow_image:  # read_image：工具结果后再补一条带图消息，让模型真正看到图
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": f"这是 {args.get('path')} 的图片内容："}, follow_image]})
    raise DraftError(f"超过最大步数（{MAX_STEPS}），已停止。可以补充说明后再发一条消息继续。")


def _tool_brief(name, args):
    briefs = {"query_students": "正在查询班级与学生名单…",
              "get_lesson_context": "正在读取目标课次信息…",
              "read_image": f"正在查看图片 {args.get('path', '')}…",
              "parse_excel": f"正在解析表格 {args.get('path', '')}…",
              "parse_pdf": f"正在提取试卷文字 {args.get('path', '')}…",
              "fetch_url": f"正在抓取网页 {args.get('url', '')}…",
              "web_search": f"正在联网搜索「{args.get('query', '')}」…",
              "save_draft": f"正在保存草稿「{args.get('title', '')}」…"}
    return briefs.get(name, f"正在调用 {name}…")


def _dispatch(ctx, name, args, drafts):
    if name == "query_students": return _tool_query_students(ctx), None
    if name == "list_lessons": return _tool_list_lessons(ctx), None
    if name == "get_lesson_context": return _tool_lesson_context(ctx), None
    if name == "read_image": return _tool_read_image(ctx, args)
    if name == "parse_excel": return _tool_parse_excel(ctx, args), None
    if name == "parse_pdf": return _tool_parse_pdf(ctx, args), None
    if name == "fetch_url": return _tool_fetch_url(args), None
    if name == "web_search": return _tool_web_search(args), None
    if name == "save_draft": return _tool_save_draft(ctx, args, drafts), None
    raise DraftError(f"未知工具 {name}")


def _safe_upload(ctx, raw, exts):
    """校验 uploads/ 相对路径，防目录穿越。"""
    p = (raw or "").strip()
    if not p.startswith("uploads/"):
        raise DraftError("路径必须是 uploads/ 开头的已上传文件")
    file = ctx.uploads_root.parent / p
    if not file.is_file() or ctx.uploads_root not in file.parents:
        raise DraftError("文件不存在或不在上传目录内")
    if exts and file.suffix.lower() not in exts:
        raise DraftError(f"只支持 {'/'.join(sorted(exts))} 文件")
    return file


# ---------------------------------------------------------------- 读类工具

def _tool_query_students(ctx):
    data = ctx.data_snapshot()
    out = [{"class": c["name"], "students": [s["name"] for s in data["students"] if s["classId"] == c["id"]]}
           for c in data["classes"]]
    return json.dumps({"classes": out}, ensure_ascii=False)


def _tool_list_lessons(ctx):
    data = ctx.data_snapshot()
    out = []
    for l in sorted(data["lessons"], key=lambda x: (x.get("date", ""), x.get("period", "")), reverse=True)[:30]:
        cls = next((c["name"] for c in data["classes"] if c["id"] == l.get("classId")), "")
        a = next((a for a in data["assignments"] if a["id"] == l.get("assignmentId")), {})
        out.append({"lessonId": l["id"], "date": l.get("date"), "period": l.get("period"),
                    "class": cls, "title": a.get("title", "")})
    return json.dumps({"lessons": out, "当前会话绑定课次": ctx.lesson.get("id") if ctx.lesson else "无（保存草稿时必须提供 lessonId）"},
                      ensure_ascii=False)


def _tool_lesson_context(ctx):
    lesson, data = ctx.lesson, ctx.data_snapshot()
    if not lesson: return json.dumps({"error": "本会话未关联课次"}, ensure_ascii=False)
    assignment = next((a for a in data["assignments"] if a["id"] == lesson.get("assignmentId")), {})
    cls = next((c for c in data["classes"] if c["id"] == lesson.get("classId")), {})
    students = [s["name"] for s in data["students"] if s["classId"] == lesson.get("classId")]
    return json.dumps({"date": lesson.get("date"), "period": lesson.get("period"),
                       "class": cls.get("name"), "title": assignment.get("title"),
                       "knowledgePoints": assignment.get("knowledgePoints", []),
                       "students": students}, ensure_ascii=False)


def _tool_read_image(ctx, args):
    file = _safe_upload(ctx, args.get("path"), {".png", ".jpg", ".jpeg", ".webp", ".gif"})
    return "图片已加载，请查看随后消息中的图片内容。", ctx.image_message(f"uploads/{file.name}")


def _tool_parse_excel(ctx, args):
    file = _safe_upload(ctx, args.get("path"), {".xlsx", ".xls", ".csv"})
    if file.suffix.lower() == ".csv":
        import csv
        with open(file, newline="", encoding="utf-8-sig") as fh:
            rows = [r[:MAX_EXCEL_COLS] for r in list(csv.reader(fh))[:MAX_EXCEL_ROWS]]
        return json.dumps({"sheets": [{"name": file.name, "rows": rows}]}, ensure_ascii=False)
    try:
        import openpyxl
    except ImportError:
        raise DraftError("服务器未安装 openpyxl，请先运行：.venv/bin/pip install -r requirements.txt")
    try:
        wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    except Exception as exc:
        raise DraftError(f"无法解析该表格：{exc}（.xls 老格式请先在 Excel 里另存为 .xlsx）")
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 0, MAX_EXCEL_ROWS),
                                max_col=min(ws.max_column or 0, MAX_EXCEL_COLS), values_only=True):
            rows.append(["" if v is None else str(v)[:80] for v in row])
        sheets.append({"name": ws.title, "rows": rows})
    wb.close()
    text = json.dumps({"sheets": sheets}, ensure_ascii=False)
    return text[:MAX_TEXT_CHARS] + ("…（表格过大已截断）" if len(text) > MAX_TEXT_CHARS else "")


def _tool_parse_pdf(ctx, args):
    file = _safe_upload(ctx, args.get("path"), {".pdf"})
    try:
        import pdfplumber
    except ImportError:
        raise DraftError("服务器未安装 pdfplumber，请先运行：.venv/bin/pip install -r requirements.txt")
    pages = []
    with pdfplumber.open(file) as pdf:
        for i, page in enumerate(pdf.pages[:MAX_PDF_PAGES]):
            pages.append(f"--- 第 {i + 1} 页 ---\n{page.extract_text() or ''}")
    text = "\n".join(pages).strip()
    if not text.replace("---", "").strip():
        return "该 PDF 提取不到文字（可能是扫描件）。请让老师改传试卷照片（jpg/png），然后你用 read_image 查看。"
    return text[:MAX_TEXT_CHARS] + ("…（内容过长已截断）" if len(text) > MAX_TEXT_CHARS else "")


def _tool_fetch_url(args):
    url = (args.get("url") or "").strip()
    _check_url(url)
    try:
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=FETCH_TIMEOUT) as res:
            ctype = res.headers.get("Content-Type", "")
            raw = res.read(MAX_FETCH_BYTES)
    except HTTPError as exc:
        raise DraftError(f"网页返回 {exc.code}（需要登录或已拦截）")
    except URLError as exc:
        raise DraftError(f"无法访问该网址：{exc.reason}")
    if "text/html" not in ctype and "text/plain" not in ctype and "json" not in ctype:
        raise DraftError(f"该链接不是网页（{ctype}），无法读取正文")
    text = raw.decode("utf-8", "replace")
    if "text/html" in ctype:
        text = _html_to_text(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise DraftError("抓到的页面没有正文（可能需要登录，或是动态渲染页面）")
    return text[:MAX_TEXT_CHARS] + ("…（页面过长已截断）" if len(text) > MAX_TEXT_CHARS else "")


def _tool_web_search(args):
    query = (args.get("query") or "").strip()
    if not query: raise DraftError("搜索关键词不能为空")
    url = "https://html.duckduckgo.com/html/?q=" + quote(query)
    try:
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=FETCH_TIMEOUT) as res:
            html = res.read(300_000).decode("utf-8", "replace")
    except (HTTPError, URLError, socket.timeout) as exc:
        raise DraftError(f"搜索失败：{exc}")
    results = _parse_ddg(html)
    if not results:
        return "没有搜到结果（搜索引擎可能拦截了请求）。可改用 fetch_url 直接抓取已知网址。"
    lines = [f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet']}" for i, r in enumerate(results)]
    return "搜索结果（可用 fetch_url 打开其中链接看详情）：\n" + "\n".join(lines)


def _tool_save_draft(ctx, args, drafts):
    title = (args.get("title") or "").strip()
    rows = args.get("rows") or []
    if not title: raise DraftError("草稿需要标题")
    if not isinstance(rows, list) or not rows: raise DraftError("草稿至少需要一行数据")
    data = ctx.data_snapshot()
    lesson_id = (args.get("lessonId") or "").strip() or (ctx.lesson.get("id") if ctx.lesson else "")
    if not any(l["id"] == lesson_id for l in data["lessons"]):
        raise DraftError("草稿必须归属到一节课：请先用 list_lessons 列出课次并与老师确认目标课次，再把 lessonId 传给我")
    roster = {}  # 姓名 -> studentId，用于服务端二次核对（不信任模型给的 id）
    for s in data["students"]:
        roster.setdefault(re.sub(r"\s+", "", s["name"]), s["id"])
    cleaned, unmatched = [], []
    for r in rows[:200]:
        name = str(r.get("studentName", "")).strip()
        sid = roster.get(re.sub(r"\s+", "", name))
        if not sid: unmatched.append(name)
        cleaned.append({"studentName": name, "studentId": sid or "",
                        "question": str(r.get("question", "")).strip() or "未识别题目",
                        "knowledgePoint": str(r.get("knowledgePoint", "")).strip() or "待确认",
                        "errorType": str(r.get("errorType", "")).strip() or "待老师确认",
                        "score": str(r.get("score", "")).strip(),
                        "confidence": max(0.0, min(1.0, float(r.get("confidence", 0) or 0))),
                        "note": str(r.get("note", "")).strip()})
    draft = {"title": title, "summary": str(args.get("summary", "")).strip(), "lessonId": lesson_id,
             "rows": cleaned, "unmatched": sorted({n for n in unmatched if n})}
    drafts.append(draft)
    ctx.emit("draft", f"草稿已保存：{title}（{len(cleaned)} 行"
                      + (f"，{len(draft['unmatched'])} 人未匹配名单" if draft["unmatched"] else "") + "）",
             draft=draft)
    return json.dumps({"ok": True, "draftIndex": len(drafts) - 1, "rows": len(cleaned),
                       "unmatched": draft["unmatched"],
                       "提示": "草稿已保存，老师确认后才会导入系统"}, ensure_ascii=False)


# ---------------------------------------------------------------- 网页工具内部

_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "metadata.google.internal"}


def _check_url(url):
    """SSRF 防护：只允许 http/https，禁止内网与本机地址。"""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise DraftError("只允许 http/https 链接")
    host = p.hostname.lower()
    if host in _BLOCKED_HOSTS:
        raise DraftError("不允许访问本机地址")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise DraftError("不允许访问内网地址")
    except ValueError:
        try:  # 域名解析后再查一次，防 DNS 指向内网
            for info in socket.getaddrinfo(host, None):
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    raise DraftError("该网址解析到内网地址，已拦截")
        except socket.gaierror:
            raise DraftError("域名无法解析")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"): self.skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "article"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip: self.skip -= 1

    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def _html_to_text(html):
    ex = _TextExtractor()
    try: ex.feed(html)
    except Exception: pass
    text = re.sub(r"[ \t]+", " ", "".join(ex.parts))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


class _DDGParser(HTMLParser):
    """解析 DuckDuckGo HTML 版结果页：a.result__a（标题+链接）与 a.result__snippet（摘要）。"""
    def __init__(self):
        super().__init__()
        self.results, self.cur, self.field = [], None, None

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "")
        if tag == "a" and "result__a" in cls:
            href = dict(attrs).get("href", "")
            m = parse_qs(urlparse(href).query).get("uddg")  # 跳转链接里的真实地址
            self.cur = {"title": "", "url": unquote(m[0]) if m else href, "snippet": ""}
            self.field = "title"
        elif tag == "a" and "result__snippet" in cls and self.cur is not None:
            self.field = "snippet"

    def handle_endtag(self, tag):
        if tag == "a" and self.field:
            self.field = None
            if self.cur is not None and self.cur["title"] and self.cur not in self.results:
                self.results.append(self.cur)

    def handle_data(self, data):
        if self.field and self.cur is not None:
            self.cur[self.field] += data


def _parse_ddg(html):
    p = _DDGParser()
    try: p.feed(html)
    except Exception: pass
    out = []
    for r in p.results:
        r["title"], r["snippet"] = r["title"].strip(), re.sub(r"\s+", " ", r["snippet"]).strip()
        if r["title"] and r["url"].startswith("http"): out.append(r)
        if len(out) >= MAX_SEARCH_RESULTS: break
    return out
