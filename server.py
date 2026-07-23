#!/usr/bin/env python3
"""本地运行的学生错题管理与统计系统。"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime
import base64, hashlib, hmac, json, mimetypes, os, re, secrets, sqlite3, time, uuid

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "data.json"
# 密钥与账号保存在项目外的用户目录，避免打包 zip 或推送 GitHub 时泄露。
CONFIG_DIR = Path.home() / ".student-stats"
CONFIG_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = CONFIG_DIR / "settings.json"
USERS_FILE = CONFIG_DIR / "users.json"
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)

DEFAULT = {"classes": [], "students": [], "assignments": [], "mistakes": [], "seats": [], "performances": []}
MODELS = {
    "qwen3.7-plus": {"provider": "阿里云百炼", "providerKey":"qwen", "model": "qwen3.7-plus", "label": "Qwen3.7 Plus｜效果优先"},
    "qwen3.6-flash": {"provider": "阿里云百炼", "providerKey":"qwen", "model": "qwen3.6-flash", "label": "Qwen3.6 Flash｜推荐"},
    "qwen3-vl-flash": {"provider": "阿里云百炼", "providerKey":"qwen", "model": "qwen3-vl-flash", "label": "Qwen3-VL Flash｜速度优先"},
    "kimi-k3": {"provider": "Kimi（月之暗面）", "providerKey":"kimi", "model": "kimi-k3", "label": "Kimi K3｜长图文理解"},
    "mimo-v2.5-pro": {"provider": "小米 MiMo（Token Plan）", "providerKey":"mimo", "model": "mimo-v2.5-pro", "label": "MiMo V2.5 Pro｜效果优先"},
    "mimo-v2.5": {"provider": "小米 MiMo（Token Plan）", "providerKey":"mimo", "model": "mimo-v2.5", "label": "MiMo V2.5｜速度优先"},
    "deepseek-v4-pro": {"provider": "DeepSeek", "providerKey":"deepseek", "model": "deepseek-v4-pro", "label": "DeepSeek V4 Pro｜效果优先（仅文本，不可用于作业识别）"},
    "deepseek-v4-flash": {"provider": "DeepSeek", "providerKey":"deepseek", "model": "deepseek-v4-flash", "label": "DeepSeek V4 Flash｜速度优先（仅文本，不可用于作业识别）"},
}
DEFAULT_SETTINGS = {"model": "qwen3.6-flash", "apiKeys": {}}

# 登录令牌有效期：7 天；密码哈希：PBKDF2-HMAC-SHA256（Python 标准库实现，代替指南中的 BCrypt）。
TOKEN_TTL = 7 * 24 * 3600
PBKDF2_ITERATIONS = 100_000
# 内置管理员账号：首次启动时自动创建。
ADMIN_USERNAME, ADMIN_PASSWORD = "root", "change-me-before-first-run"
ROLES = {"admin": "管理员", "teacher": "老师", "student": "学生"}

def read_data():
    if not DATA_FILE.exists():
        return DEFAULT.copy()
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        for key in DEFAULT: data.setdefault(key, [])
        return data
    except (json.JSONDecodeError, OSError):
        return DEFAULT.copy()

def write_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def read_settings():
    if not SETTINGS_FILE.exists(): return DEFAULT_SETTINGS.copy()
    try:
        settings = {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))}
        settings["apiKeys"] = dict(settings.get("apiKeys", {}))
        # 兼容旧版本中只保存一个 apiKey 的设置。
        if settings.get("apiKey"):
            selected = MODELS.get(settings["model"], MODELS["qwen3.6-flash"])
            settings["apiKeys"].setdefault(selected["providerKey"], settings["apiKey"])
        return settings
    except (json.JSONDecodeError, OSError): return DEFAULT_SETTINGS.copy()

def write_settings(settings):
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

def migrate_local_secrets():
    """把早期版本放在项目目录里的 settings.json / users.json 迁出，避免随代码打包外泄。"""
    for name in ("settings.json", "users.json"):
        local = ROOT / name
        if not local.exists(): continue
        target = CONFIG_DIR / name
        try:
            if target.exists():
                backup = CONFIG_DIR / (name + ".backup")
                local.replace(backup)
                print(f"[学生管理系统] 项目目录中的 {name} 已移到 {backup}，请核对后自行删除")
            else:
                local.replace(target)
                print(f"[学生管理系统] 已把 {name} 迁移到 {CONFIG_DIR}，项目目录不再保存密钥")
        except OSError as exc:
            print(f"[学生管理系统] 迁移 {name} 失败：{exc}")

def model_api_key(choice, settings):
    """API Key 优先取环境变量 STUDENT_STATS_QWEN_KEY / _KIMI_KEY / _MIMO_KEY，其次取设置页保存的。"""
    return os.environ.get(f"STUDENT_STATS_{choice['providerKey'].upper()}_KEY", "").strip() or settings.get("apiKeys", {}).get(choice["providerKey"], "").strip()

def model_client(choice, key):
    endpoints = {"qwen":"https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "kimi":"https://api.moonshot.ai/v1/chat/completions", "mimo":"https://token-plan-cn.xiaomimimo.com/v1/chat/completions", "deepseek":"https://api.deepseek.com/v1/chat/completions"}
    return endpoints[choice["providerKey"]], {"Authorization": f"Bearer {key}"}

def test_model_connection(settings, model_id, api_key=""):
    """用一次最小真实调用验证模型与 Key 是否可用，成功返回模型回复。"""
    choice = MODELS.get(model_id)
    if not choice: raise ValueError("请选择列表中的模型")
    key = api_key.strip() or model_api_key(choice, settings)
    if not key: raise ValueError("请先填写或保存 API Key")
    url, headers = model_client(choice, key)
    out = http_json(url, {"model": choice["model"], "messages": [{"role": "user", "content": "只回复两个字：正常"}], "max_tokens": 64}, headers, timeout=60)
    msg = out["choices"][0]["message"]
    return (msg.get("content") or msg.get("reasoning_content") or "").strip() or "（连接成功，模型未返回文本）"

CC_SWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"

def import_cc_switch_mimo_key():
    """从 CC Switch 的配置库中读取小米 MiMo Token Plan 的 API Key（只读，不修改其配置）。"""
    if not CC_SWITCH_DB.exists(): raise ValueError("没有找到 CC Switch 配置（~/.cc-switch/cc-switch.db）")
    try:
        db = sqlite3.connect(f"file:{CC_SWITCH_DB}?mode=ro", uri=True)
        try:
            row = db.execute("select settings_config from providers where id = 'xiaomi' and app_type = 'hermes'").fetchone()
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise ValueError(f"无法读取 CC Switch 配置：{exc}")
    if not row: raise ValueError("CC Switch 中没有找到小米 MiMo Token Plan 配置")
    key = (json.loads(row[0]).get("api_key") or "").strip()
    if not key: raise ValueError("CC Switch 中的小米配置没有 API Key")
    return key

def read_users():
    if not USERS_FILE.exists(): return {"secret": secrets.token_hex(32), "users": []}
    try:
        store = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        store.setdefault("secret", secrets.token_hex(32))
        store.setdefault("users", [])
        return store
    except (json.JSONDecodeError, OSError):
        return {"secret": secrets.token_hex(32), "users": []}

def write_users(store):
    USERS_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")

def public_user(u):
    return {"id": u["id"], "username": u["username"], "realName": u.get("realName", ""), "role": u.get("role", "teacher"), "lastLoginAt": u.get("lastLoginAt", "")}

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt}${digest}"

def verify_password(password, stored):
    try:
        _, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, AttributeError):
        return False

def make_token(store, user):
    payload = {"sub": user["id"], "username": user["username"], "role": user.get("role", "teacher"), "exp": int(time.time()) + TOKEN_TTL}
    body = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).rstrip(b"=").decode("ascii")
    sig = hmac.new(store["secret"].encode("ascii"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"

def parse_token(store, token):
    try:
        body, sig = token.split(".")
        expected = hmac.new(store["secret"].encode("ascii"), body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected): return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
        if payload.get("exp", 0) < time.time(): return None
        return payload
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None

def seed_admin():
    """首次启动时创建内置管理员账号 root。"""
    store = read_users()
    if not any(u.get("role") == "admin" for u in store["users"]):
        store["users"].append({"id": make_id("user"), "username": ADMIN_USERNAME, "realName": "管理员", "role": "admin",
                               "passwordHash": hash_password(ADMIN_PASSWORD), "createdAt": datetime.now().isoformat(timespec="seconds"), "lastLoginAt": ""})
        write_users(store)
        print(f"[学生管理系统] 已创建内置管理员账号：{ADMIN_USERNAME}")

def make_user(username, password, real_name, role):
    return {"id": make_id("user"), "username": username, "realName": real_name, "role": role,
            "passwordHash": hash_password(password), "createdAt": datetime.now().isoformat(timespec="seconds"), "lastLoginAt": ""}

def validate_account(username, password, role, real_name=""):
    if not re.fullmatch(r"[\w-]{2,20}", username): raise ValueError("用户名需为 2-20 位，可含中文、字母、数字")
    if len(password) < 6: raise ValueError("密码至少 6 位")
    if role not in ("teacher", "student"): raise ValueError("账号身份只能是老师或学生")
    if role == "student" and not real_name: raise ValueError("学生账号需填写与班级名单一致的姓名")

def filtered_data(user):
    """按角色过滤业务数据：管理员看全部；老师看自己创建的班级；学生只看自己的错题。"""
    data = read_data()
    role = user.get("role", "teacher")
    if role == "admin": return data
    if role == "teacher":
        classes = [c for c in data["classes"] if c.get("ownerId") in (None, "", user["id"])]
        class_ids = {c["id"] for c in classes}
        students = [s for s in data["students"] if s["classId"] in class_ids]
        assignments = [a for a in data["assignments"] if a["classId"] in class_ids]
        assignment_ids = {a["id"] for a in assignments}
        student_ids = {s["id"] for s in students}
        mistakes = [m for m in data["mistakes"] if m.get("assignmentId") in assignment_ids or m.get("studentId") in student_ids]
        seats = [s for s in data["seats"] if s["classId"] in class_ids]
        performances = [p for p in data["performances"] if p["classId"] in class_ids]
        return {"classes": classes, "students": students, "assignments": assignments, "mistakes": mistakes, "seats": seats, "performances": performances}
    name = re.sub(r"\s+", "", user.get("realName", "") or user["username"])
    mine = [s for s in data["students"] if re.sub(r"\s+", "", s["name"]) == name]
    student_ids = {s["id"] for s in mine}
    mistakes = [m for m in data["mistakes"] if m.get("studentId") in student_ids]
    assignment_ids = {m["assignmentId"] for m in mistakes}
    assignments = [a for a in data["assignments"] if a["id"] in assignment_ids]
    performances = [p for p in data["performances"] if p.get("studentId") in student_ids]
    return {"classes": [], "students": mine, "assignments": assignments, "mistakes": mistakes, "seats": [], "performances": performances}

def make_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"

def image_part(path):
    file = ROOT / path
    if not file.is_file() or UPLOADS not in file.parents: raise ValueError("找不到已上传的图片")
    mime = mimetypes.guess_type(file.name)[0] or "image/jpeg"
    return base64.b64encode(file.read_bytes()).decode("ascii"), mime

def http_json(url, payload, headers, timeout=120):
    req = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type":"application/json", **headers}, method="POST")
    try:
        with urlopen(req, timeout=timeout) as res: return json.loads(res.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try: detail = json.loads(detail).get("error", {}).get("message", detail)
        except json.JSONDecodeError: pass
        raise ValueError(f"模型服务返回错误：{detail}")
    except URLError as exc: raise ValueError(f"无法连接模型服务：{exc.reason}")

def extract_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start: raise ValueError("模型没有返回可读取的分析结果，请更换清晰图片后重试")
    return json.loads(text[start:end + 1])

def ask_agent(settings, prompt, report_paths, work_paths):
    choice = MODELS.get(settings["model"])
    if not choice: raise ValueError("请选择一个支持图片分析的模型")
    key = model_api_key(choice, settings)
    if not key: raise ValueError("请先在“设置”中填写 API Key")
    images = [("课堂内容报告", *image_part(p)) for p in report_paths] + [("学生作业", *image_part(p)) for p in work_paths]
    content = [{"type":"text", "text":prompt}]
    for label, raw, mime in images:
        content += [{"type":"text", "text":f"以下是{label}："}, {"type":"image_url", "image_url":{"url":f"data:{mime};base64,{raw}"}}]
    url, headers = model_client(choice, key)
    out = http_json(url, {"model":choice["model"], "messages":[{"role":"user","content":content}], "response_format":{"type":"json_object"}}, headers)
    text = out["choices"][0]["message"]["content"]
    return extract_json(text)

AGENT_PROMPT = '''你是严谨的中小学教师错题分析助手。分析课堂内容报告与学生作业图片，提取课堂主题、知识点，并识别学生错题。绝对不要猜测：图片模糊、姓名不清、正确答案无法确定或分类不确定时，必须标记为待老师确认。仅返回一个 JSON 对象，不要 Markdown：
{"title":"本次作业名称","lessonSummary":"课堂内容摘要","knowledgePoints":["知识点"],"students":[{"name":"学生姓名"}],"mistakes":[{"studentName":"学生姓名或未知学生","question":"题号与简短题目","knowledgePoint":"知识点或待确认","errorType":"知识点不理解/计算错误/审题错误/步骤不完整/答案表达错误/未作答/其他/待老师确认","status":"confirmed或pending","confidence":0到1之间数字,"note":"判断依据或需老师确认的原因"}]}'''

REPORT_PROMPT = '''你是严谨的中小学课堂内容整理助手。分析课堂内容报告（教案、板书或课堂记录）图片，提取本节课的主题、内容摘要与知识点。绝对不要猜测：图片模糊或内容无法确定时，在摘要中注明需老师补充。仅返回一个 JSON 对象，不要 Markdown：
{"title":"本节课名称","lessonSummary":"课堂内容摘要","knowledgePoints":["知识点"]}'''

# ---------------- 学习总结 Agent ----------------
# Agent 的“灵魂”保存在 agent.md，老师可在“设置”页直接修改。
AGENT_FILE = ROOT / "agent.md"
AGENT_OUTPUT_DIR = ROOT / "agent输出"
DEFAULT_SOUL = '''# 学习总结 Agent 灵魂设定

你是一名中小学课后学习总结助手，服务对象是老师和家长。

## 你的任务
1. 根据老师提供的做题截图，分析学生的做题情况：完成了哪些题、错在哪里、反映出哪些知识点掌握情况。
2. 把老师输入的课堂表现（可能是口语化、碎片化的）润色为家长容易接受的书面表述：先肯定优点，再委婉指出可改进处，最后给出可执行的建议。
3. 汇总成一篇本节课的学习总结（Markdown 格式）。

## 输出格式
## 学生姓名
#### 课堂表现：
（润色后的段落）
#### 知识点掌握：
（结合做题情况的分析段落）
#### 本堂课学习内容：
（有序列表）
#### 完成题目：
（从做题截图中逐题识别；算法题带链接）
#### 课后建议：
（具体、可执行的建议）

## 题目识别与链接
- 「完成题目」必须逐题来自做题截图的识别结果（题号、题目名称、通过/得分状态），不得凭印象或知识点推测列举；截图看不清的题目标注“待确认”。
- 算法/在线评测题目（如 P1487）按此格式生成链接：[**P1487 题目名称**](https://chuanshuo.com.cn/d/CPP2026_03/p/P1487)，即 https://chuanshuo.com.cn/d/题集代码/p/题号。题集代码按班级确定（如 12 班为 CPP2026_12、3 班为 CPP2026_03），无法确定时用 CPP2026_03 或向老师确认。

## 红线
- 只依据提供的材料（做题截图、错题记录、老师描述的课堂表现）写作，绝不编造题目、分数、名次或表现。
- 「题目截图」小节由系统按真实截图自动附加到文档末尾，正文中不要自己编写图片链接或文件路径。
- 某部分材料不足时，如实写明“本节课材料不足，未做评估”，不要虚构内容补足结构。
- 语气对家长友好、具体、不夸张，不使用“差”“不行”等定性词。
'''

def read_soul():
    if not AGENT_FILE.exists():
        try: AGENT_FILE.write_text(DEFAULT_SOUL, encoding="utf-8")
        except OSError: pass
    try: return AGENT_FILE.read_text(encoding="utf-8")
    except OSError: return DEFAULT_SOUL

def resolve_save_path(raw, student, lesson):
    raw = (raw or "").strip()
    if not raw: raw = f"{student['name']}-{lesson.get('date') or '未排期'}.md"
    if not raw.lower().endswith(".md"): raw += ".md"
    path = Path(raw)
    return path if path.is_absolute() else AGENT_OUTPUT_DIR / path

def build_agent_context(student, lesson, mistakes, perfs, image_count, extra=""):
    lines = [f"学生：{student['name']}",
             f"课程：{lesson.get('date') or '未排期'} 第{lesson.get('period') or '?'}节《{lesson.get('title', '未命名课程')}》",
             f"课堂知识点：{'、'.join(lesson.get('knowledgePoints', [])) or '未记录'}",
             f"课堂摘要：{lesson.get('content', '') or '未记录'}"]
    for p in perfs:
        detail = "；".join(x for x in [("标签：" + "、".join(p["tags"])) if p.get("tags") else "", p.get("note", ""), ("做题情况：" + p["workNote"]) if p.get("workNote") else ""] if x)
        if detail: lines.append(f"课堂表现（老师原始记录）：{detail}")
    if mistakes:
        lines.append("错题记录：")
        lines += [f"- {m.get('question', '未识别题目')}（知识点：{m.get('knowledgePoint', '待确认')}，错误类型：{m.get('errorType', '待老师确认')}）" for m in mistakes]
    lines.append(f"做题截图：共 {image_count} 张" + ("（见附图）" if image_count else ""))
    if extra: lines.append(f"老师补充说明：{extra}")
    return "\n".join(lines)

def build_overview_context(data):
    """把系统中的班级、课程和学生记录压缩成概览，让 Agent 在会话中“看得见”数据。"""
    lines = ["以下是系统中已有的真实数据，你可以直接引用回答，不要声称看不到；没有记录就如实说明："]
    for c in data["classes"]:
        names = "、".join(s["name"] for s in data["students"] if s["classId"] == c["id"]) or "暂无学生"
        lines.append(f"班级：{c['name']}（{c.get('grade') or '未填年级'}）学生：{names}")
    lessons = data["assignments"][-20:]
    if lessons:
        lines.append("课程记录：")
        for a in reversed(lessons):
            cls = next((c["name"] for c in data["classes"] if c["id"] == a["classId"]), "未分班")
            lines.append(f"- {a.get('date') or '未排期'} 第{a.get('period') or '?'}节 {cls}《{a.get('title', '未命名课程')}》")
    for s in data["students"]:
        ms = [m for m in data["mistakes"] if m.get("studentId") == s["id"]]
        ps = [p for p in data["performances"] if p.get("studentId") == s["id"]]
        if not ms and not ps: continue
        lines.append(f"学生「{s['name']}」的记录：")
        for m in ms[-5:]:
            lines.append(f"  错题：{m.get('question', '')}（{m.get('knowledgePoint', '')}，{m.get('errorType', '')}）")
        for p in ps[-5:]:
            lesson = next((a for a in data["assignments"] if a["id"] == p.get("assignmentId")), None)
            ldesc = f"{lesson.get('date', '')} 第{lesson.get('period') or '?'}节" if lesson else "未关联课程"
            flags = []
            if p.get("tags"): flags.append("标签：" + "、".join(p["tags"]))
            if p.get("note"): flags.append("课堂记录：" + p["note"])
            if p.get("workNote"): flags.append("做题情况：" + p["workNote"])
            flags.append("有做题截图" if p.get("image") and (ROOT / p["image"]).is_file() else "无做题截图")
            lines.append(f"  {ldesc}：" + "；".join(flags))
    return "\n".join(lines)

def attach_screenshots(md_text, image_paths, base_name, out_dir):
    """把真实做题截图复制到文档旁的 images/ 目录，并在文末生成“题目截图”小节（图片链接只由系统生成，防止编造）。"""
    md_text = re.sub(r"\n*#{2,4}\s*(题目|完成情况)截图[：:]?.*$", "", md_text, flags=re.S).rstrip()
    if not image_paths: return md_text
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    parts = [md_text, "", "#### 题目截图：", ""]
    for i, img in enumerate(image_paths, 1):
        src = ROOT / img
        if not src.is_file(): continue
        dst = img_dir / f"{base_name}-{i}{src.suffix or '.png'}"
        dst.write_bytes(src.read_bytes())
        parts += [f"![{base_name}](images/{dst.name})", ""]
    return "\n".join(parts)

def chat_model(settings, system_prompt, messages):
    """以 system_prompt 为“灵魂”调用所选模型；messages 为 OpenAI 兼容消息列表。"""
    choice = MODELS.get(settings["model"])
    if not choice: raise ValueError("请选择一个支持图片分析的模型")
    key = model_api_key(choice, settings)
    if not key: raise ValueError("请先在“设置”中填写 API Key")
    url, headers = model_client(choice, key)
    out = http_json(url, {"model":choice["model"], "messages":[{"role":"system", "content":system_prompt}] + messages}, headers)
    return out["choices"][0]["message"]["content"]

class App(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[学生管理系统] " + fmt % args)

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers(); self.wfile.write(payload)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def current_user(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "): return None
        store = read_users()
        payload = parse_token(store, header[7:])
        if not payload: return None
        return next((u for u in store["users"] if u["id"] == payload.get("sub")), None)

    def check_auth(self):
        """鉴权守卫：/api/auth/**、/api/health 与静态页面放行，其余 /api/** 需要有效登录令牌。"""
        path = urlparse(self.path).path
        if not path.startswith("/api/") or path.startswith("/api/auth/") or path == "/api/health": return True
        user = self.current_user()
        if user:
            self.user = user
            return True
        self.send_json({"error": "请先登录"}, 401)
        return False

    def require_admin(self):
        if self.user.get("role") != "admin":
            self.send_json({"error": "仅管理员可操作"}, 403)
            return False
        return True

    def do_GET(self):
        if not self.check_auth(): return
        path = urlparse(self.path).path
        if path == "/api/auth/me":
            user = self.current_user()
            if not user: return self.send_json({"error": "请先登录"}, 401)
            return self.send_json({"user": public_user(user)})
        if path == "/api/agent/soul":
            if self.user.get("role") == "student": return self.send_json({"error": "学生账号无权查看"}, 403)
            return self.send_json({"soul": read_soul()})
        if path == "/api/users":
            if not self.require_admin(): return
            store = read_users()
            return self.send_json({"users": [public_user(u) for u in store["users"]]})
        if path == "/api/data": return self.send_json(filtered_data(self.user))
        if path == "/api/settings":
            s = read_settings(); selected=MODELS.get(s["model"], MODELS["qwen3.6-flash"])
            saved = {}
            for m in MODELS.values(): saved[m["providerKey"]] = saved.get(m["providerKey"]) or bool(model_api_key(m, s))
            return self.send_json({"model":s["model"], "provider":selected["provider"], "hasApiKey":bool(model_api_key(selected, s)), "savedKeys":saved, "models":[{"id":k, **v} for k,v in MODELS.items()]})
        if path == "/api/health": return self.send_json({"ok": True})
        return super().do_GET()

    def do_POST(self):
        if not self.check_auth(): return
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/auth/register":
                username, password = body.get("username", "").strip(), body.get("password", "")
                real_name, role = body.get("realName", "").strip(), body.get("role", "teacher")
                validate_account(username, password, role, real_name)
                store = read_users()
                if any(u["username"] == username for u in store["users"]): raise ValueError("该用户名已被注册")
                user = make_user(username, password, real_name, role)
                store["users"].append(user); write_users(store)
                return self.send_json({"token": make_token(store, user), "user": public_user(user)}, 201)
            if path == "/api/auth/login":
                store = read_users()
                user = next((u for u in store["users"] if u["username"] == body.get("username", "").strip()), None)
                if not user or not verify_password(body.get("password", ""), user.get("passwordHash", "")):
                    raise ValueError("用户名或密码不正确")
                user["lastLoginAt"] = datetime.now().isoformat(timespec="seconds"); write_users(store)
                return self.send_json({"token": make_token(store, user), "user": public_user(user)})
            if path == "/api/auth/logout":
                return self.send_json({"ok": True})
            if self.user.get("role") == "student":
                return self.send_json({"error": "学生账号仅可查看自己的错题"}, 403)
            data = read_data()
            if path == "/api/agent/soul":
                AGENT_FILE.write_text(body.get("soul", ""), encoding="utf-8")
                return self.send_json({"ok": True})
            if path == "/api/agent/chat":
                messages = [{"role": m.get("role"), "content": str(m.get("content", ""))[:4000]} for m in body.get("messages", [])[-20:] if m.get("role") in ("user", "assistant")]
                if not messages: raise ValueError("请输入内容")
                system = read_soul() + "\n\n" + build_overview_context(data)
                student = next((s for s in data["students"] if s["id"] == body.get("studentId", "")), None)
                lesson = next((a for a in data["assignments"] if a["id"] == body.get("assignmentId", "")), None)
                if student and lesson:
                    mistakes = [m for m in data["mistakes"] if m.get("studentId") == student["id"] and m.get("assignmentId") == lesson["id"]]
                    perfs = [p for p in data["performances"] if p.get("studentId") == student["id"] and p.get("assignmentId", "") == lesson["id"]]
                    system += "\n\n当前对话选中的学生与课程详情：\n" + build_agent_context(student, lesson, mistakes, perfs, 0)
                # 消息中提到学生姓名时，收集ta的做题截图：多模态模型直接附图分析，生成的文档末尾也会附上截图小节。
                settings = read_settings()
                choice = MODELS.get(settings["model"], {})
                user_text = messages[-1]["content"] if messages[-1]["role"] == "user" else ""
                mentioned = [s for s in data["students"] if s["name"] and s["name"] in user_text]
                mentioned_imgs = []
                for st in mentioned:
                    for rec in [p for p in data["performances"] if p.get("studentId") == st["id"]] + [m for m in data["mistakes"] if m.get("studentId") == st["id"]]:
                        img = rec.get("image", "")
                        if img and (ROOT / img).is_file() and img not in [x[1] for x in mentioned_imgs]: mentioned_imgs.append((st["name"], img))
                if mentioned_imgs and choice.get("providerKey") != "deepseek":
                    parts = [{"type": "text", "text": user_text}]
                    for name, img in mentioned_imgs[:3]:
                        raw, mime = image_part(img)
                        parts += [{"type": "text", "text": f"学生「{name}」的做题截图："}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{raw}"}}]
                    messages[-1] = {"role": "user", "content": parts}
                    system += "\n\n本条消息附上了学生的做题截图，请结合截图分析；没有附图则说明该学生暂无截图记录。"
                reply = chat_model(settings, system, messages)
                result = {"reply": reply}
                # 回复是完整报告（含标题结构）时，自动提取 Markdown 存成文件发到会话，无需老师额外说明。
                md_text = None
                if "####" in reply or reply.strip().startswith("##"):
                    fence = re.search(r"```(?:markdown|md)?\s*\n(.*?)```", reply, re.S)
                    md_text = (fence.group(1) if fence else reply).strip()
                if md_text:
                    who = mentioned[0]["name"] if mentioned else "学习总结"
                    if mentioned_imgs:
                        md_text = attach_screenshots(md_text, [img for _, img in mentioned_imgs[:4]], who, AGENT_OUTPUT_DIR)
                    date = time.strftime("%Y-%m-%d")
                    AGENT_OUTPUT_DIR.mkdir(exist_ok=True)
                    path, n = AGENT_OUTPUT_DIR / f"{who}-{date}.md", 1
                    while path.exists():
                        n += 1; path = AGENT_OUTPUT_DIR / f"{who}-{date}-{n}.md"
                    path.write_text(md_text, encoding="utf-8")
                    result["file"] = {"name": path.name, "url": f"/agent输出/{path.name}"}
                return self.send_json(result)
            if path == "/api/agent/generate":
                student = next((s for s in data["students"] if s["id"] == body.get("studentId", "")), None)
                lesson = next((a for a in data["assignments"] if a["id"] == body.get("assignmentId", "")), None)
                if not student: raise ValueError("请选择学生")
                if not lesson: raise ValueError("请选择课程")
                mistakes = [m for m in data["mistakes"] if m.get("studentId") == student["id"] and m.get("assignmentId") == lesson["id"]]
                perfs = [p for p in data["performances"] if p.get("studentId") == student["id"] and p.get("assignmentId", "") == lesson["id"]]
                images = []
                for rec in perfs + mistakes:
                    img = rec.get("image", "")
                    if img and (ROOT / img).is_file() and img not in images: images.append(img)
                # 红线：没有当节课的做题截图就拒绝生成，防止 Agent 凭空编造。
                if not images: raise ValueError("这名学生在这节课还没有做题截图，无法生成总结。请先在“课堂座位”中上传，避免凭空编造。")
                context = build_agent_context(student, lesson, mistakes, perfs, len(images), body.get("extra", "").strip()) + "\n\n请根据以上材料生成学习总结（Markdown）。“完成题目”必须逐题来自附图截图的识别结果，不得凭印象列举。"
                content = [{"type": "text", "text": context}]
                for img in images:
                    raw, mime = image_part(img)
                    content += [{"type": "text", "text": "做题情况截图："}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{raw}"}}]
                markdown = chat_model(read_settings(), read_soul(), [{"role": "user", "content": content}])
                save_path = resolve_save_path(body.get("savePath", ""), student, lesson)
                markdown = attach_screenshots(markdown, images, student["name"], save_path.parent)
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_text(markdown, encoding="utf-8")
                return self.send_json({"markdown": markdown, "path": str(save_path)}, 201)
            parts = path.strip("/").split("/")
            if path == "/api/users":
                if not self.require_admin(): return
                username, password = body.get("username", "").strip(), body.get("password", "")
                real_name, role = body.get("realName", "").strip(), body.get("role", "teacher")
                validate_account(username, password, role, real_name)
                store = read_users()
                if any(u["username"] == username for u in store["users"]): raise ValueError("该用户名已被注册")
                user = make_user(username, password, real_name, role)
                store["users"].append(user); write_users(store)
                return self.send_json(public_user(user), 201)
            if len(parts) == 4 and parts[:2] == ["api", "users"] and parts[3] == "password":
                if not self.require_admin(): return
                password = body.get("password", "")
                if len(password) < 6: raise ValueError("密码至少 6 位")
                store = read_users()
                user = next((u for u in store["users"] if u["id"] == parts[2]), None)
                if not user: raise ValueError("账号不存在")
                user["passwordHash"] = hash_password(password); write_users(store)
                return self.send_json({"ok": True})
            if path == "/api/settings":
                model = body.get("model", "")
                if model not in MODELS: raise ValueError("请选择列表中的模型")
                old = read_settings(); keys = old["apiKeys"]
                if body.get("apiKey", "").strip(): keys[MODELS[model]["providerKey"]] = body["apiKey"].strip()
                write_settings({"model":model, "apiKeys":keys})
                return self.send_json({"ok":True, "hasApiKey":bool(keys.get(MODELS[model]["providerKey"])), "model":model})
            if path == "/api/models/test":
                reply = test_model_connection(read_settings(), body.get("model", ""), body.get("apiKey", ""))
                return self.send_json({"ok": True, "reply": reply})
            if path == "/api/cc-switch/import":
                key = import_cc_switch_mimo_key()
                settings = read_settings(); settings["apiKeys"]["mimo"] = key
                write_settings(settings)
                return self.send_json({"ok": True, "hint": key[:6] + "****"})
            if path == "/api/analyze":
                work_paths, report_paths = body.get("workImages", []), body.get("reportImages", [])
                if not work_paths: raise ValueError("请至少上传一张学生作业照片")
                assignment_id = body.get("assignmentId", "")
                if assignment_id and not any(a["id"] == assignment_id for a in data["assignments"]):
                    raise ValueError("所选课程不存在，请重新选择")
                result = ask_agent(read_settings(), AGENT_PROMPT, report_paths, work_paths)
                if assignment_id:
                    assignment = next(a for a in data["assignments"] if a["id"] == assignment_id)
                    class_id = assignment["classId"]
                else:
                    class_id = body.get("classId", "") or (data["classes"][0]["id"] if data["classes"] else "")
                    if not class_id:
                        auto = {"id":make_id("class"), "name":"未命名班级", "grade":"", "createdAt":"", "ownerId":self.user["id"]}; data["classes"].append(auto); class_id=auto["id"]
                    assignment = {"id":make_id("assignment"), "title":result.get("title") or "AI 分析作业", "classId":class_id, "date":body.get("date", ""), "period":str(body.get("period", "")).strip(), "subject":"AI 自动识别", "content":result.get("lessonSummary", ""), "knowledgePoints":result.get("knowledgePoints", []), "createdAt":""}
                    data["assignments"].append(assignment)
                names = [x.get("name", "").strip() for x in result.get("students", [])]
                names += [x.get("studentName", "").strip() for x in result.get("mistakes", [])]
                lookup = {re.sub(r"\s+", "", s["name"]):s["id"] for s in data["students"] if s["classId"] == class_id}
                for name in dict.fromkeys(n for n in names if n and n != "未知学生"):
                    norm = re.sub(r"\s+", "", name)
                    if norm not in lookup:
                        s={"id":make_id("student"), "name":name, "classId":class_id}; data["students"].append(s); lookup[norm]=s["id"]
                saved=[]
                for m in result.get("mistakes", []):
                    norm=re.sub(r"\s+", "", m.get("studentName", "")); conf=float(m.get("confidence", 0))
                    pending=m.get("status") != "confirmed" or conf < .8 or norm not in lookup
                    item={"id":make_id("mistake"), "studentId":lookup.get(norm, ""), "assignmentId":assignment["id"], "question":m.get("question", "未能识别题目"), "knowledgePoint":m.get("knowledgePoint", "待确认"), "errorType":m.get("errorType", "待老师确认"), "status":"pending" if pending else "confirmed", "note":m.get("note", "") + ("（AI 置信度较低，需老师确认）" if pending and conf < .8 else ""), "image":work_paths[0] if work_paths else ""}
                    data["mistakes"].append(item); saved.append(item)
                write_data(data); return self.send_json({"assignment":assignment, "mistakes":saved, "lessonSummary":assignment["content"]}, 201)
            if path == "/api/lessons":
                class_id, date, period = body.get("classId", ""), body.get("date", ""), str(body.get("period", "")).strip()
                if not class_id: raise ValueError("请选择班级")
                report_paths = body.get("reportImages", [])
                if report_paths:
                    result = ask_agent(read_settings(), REPORT_PROMPT, report_paths, [])
                    title, content, kps = result.get("title") or "课堂内容报告", result.get("lessonSummary", ""), result.get("knowledgePoints", [])
                else:
                    title, content = body.get("title", "").strip(), body.get("content", "")
                    raw_kps = body.get("knowledgePoints", "")
                    kps = [k.strip() for k in re.split(r"[、,，]", raw_kps) if k.strip()] if isinstance(raw_kps, str) else raw_kps
                    if not title: raise ValueError("请上传课堂内容报告，或手动填写课程名称")
                assignment = {"id": make_id("assignment"), "title": title, "classId": class_id, "date": date, "period": period, "subject": "", "content": content, "knowledgePoints": kps, "createdAt": ""}
                data["assignments"].append(assignment)
                write_data(data); return self.send_json(assignment, 201)
            if path == "/api/seat-layout":
                class_id, rows, cols = body.get("classId", ""), int(body.get("rows", 0) or 0), int(body.get("cols", 0) or 0)
                if not (1 <= rows <= 12 and 1 <= cols <= 12): raise ValueError("座位行数和列数需在 1-12 之间")
                cls = next((c for c in data["classes"] if c["id"] == class_id), None)
                if not cls: raise ValueError("班级不存在")
                cls["seatRows"], cls["seatCols"] = rows, cols
                # 缩小布局时，清掉超出范围的座位安排。
                data["seats"] = [s for s in data["seats"] if s["classId"] != class_id or (s["row"] <= rows and s["col"] <= cols)]
                write_data(data); return self.send_json({"ok": True})
            if path == "/api/seats":
                class_id = body.get("classId", "")
                row, col = int(body.get("row", 0) or 0), int(body.get("col", 0) or 0)
                student_id = body.get("studentId", "")
                if not class_id or row < 1 or col < 1: raise ValueError("座位信息不完整")
                data["seats"] = [s for s in data["seats"] if not (s["classId"] == class_id and s["row"] == row and s["col"] == col)]
                if student_id:
                    # 一名学生在一个班只占一个座位。
                    data["seats"] = [s for s in data["seats"] if not (s["classId"] == class_id and s.get("studentId") == student_id)]
                    data["seats"].append({"id": make_id("seat"), "classId": class_id, "row": row, "col": col, "studentId": student_id})
                write_data(data); return self.send_json({"ok": True})
            if path == "/api/performances":
                class_id, assignment_id = body.get("classId", ""), body.get("assignmentId", "")
                row, col = int(body.get("row", 0) or 0), int(body.get("col", 0) or 0)
                if not class_id or row < 1 or col < 1: raise ValueError("请先选择一个座位")
                perf = next((p for p in data["performances"] if p["classId"] == class_id and p.get("assignmentId", "") == assignment_id and p["row"] == row and p["col"] == col), None)
                if not perf:
                    perf = {"id": make_id("perf"), "classId": class_id, "assignmentId": assignment_id, "row": row, "col": col}
                    data["performances"].append(perf)
                perf.update({"studentId": body.get("studentId", ""), "tags": body.get("tags", []), "note": body.get("note", ""), "workNote": body.get("workNote", ""), "updatedAt": datetime.now().isoformat(timespec="seconds")})
                if body.get("image"): perf["image"] = body["image"]
                if body.get("clearImage"): perf["image"] = ""
                write_data(data); return self.send_json(perf, 201)
            if path == "/api/classes":
                item = {"id": make_id("class"), "name": body.get("name", "未命名班级"), "grade": body.get("grade", ""), "createdAt": body.get("createdAt", ""), "ownerId": self.user["id"]}
                data["classes"].append(item)
            elif path == "/api/students":
                item = {"id": make_id("student"), "name": body.get("name", "未命名学生"), "classId": body.get("classId", "")}
                data["students"].append(item)
            elif path == "/api/assignments":
                item = {"id": make_id("assignment"), "title": body.get("title", "未命名作业"), "classId": body.get("classId", ""), "date": body.get("date", ""), "period": str(body.get("period", "")).strip(), "subject": body.get("subject", ""), "content": body.get("content", ""), "knowledgePoints": body.get("knowledgePoints", []), "createdAt": body.get("createdAt", "")}
                data["assignments"].append(item)
            elif path == "/api/mistakes":
                item = {"id": make_id("mistake"), "studentId": body.get("studentId", ""), "assignmentId": body.get("assignmentId", ""), "question": body.get("question", ""), "knowledgePoint": body.get("knowledgePoint", "待确认"), "errorType": body.get("errorType", "待老师确认"), "status": body.get("status", "confirmed"), "note": body.get("note", ""), "image": body.get("image", "")}
                data["mistakes"].append(item)
            elif path == "/api/upload":
                raw = body.get("data", "")
                if not raw.startswith("data:image/"): raise ValueError("请上传图片文件")
                header, encoded = raw.split(",", 1)
                ext = ".png" if "png" in header else ".jpg"
                name = make_id("work") + ext
                (UPLOADS / name).write_bytes(base64.b64decode(encoded))
                return self.send_json({"path": f"uploads/{name}"})
            else:
                return self.send_json({"error": "接口不存在"}, 404)
            write_data(data); return self.send_json(item, 201)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, 400)

    def do_DELETE(self):
        if not self.check_auth(): return
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "api": return self.send_json({"error":"接口不存在"}, 404)
        collection, item_id = parts[1], parts[2]
        if self.user.get("role") == "student": return self.send_json({"error": "学生账号仅可查看自己的错题"}, 403)
        if collection == "users":
            if not self.require_admin(): return
            store = read_users()
            victim = next((u for u in store["users"] if u["id"] == item_id), None)
            if not victim: return self.send_json({"error": "账号不存在"}, 404)
            if victim["username"] == ADMIN_USERNAME: return self.send_json({"error": "内置管理员账号不能删除"}, 400)
            if victim["id"] == self.user["id"]: return self.send_json({"error": "不能删除当前登录的账号"}, 400)
            store["users"] = [u for u in store["users"] if u["id"] != item_id]
            write_users(store); return self.send_json({"ok": True})
        plural = {"classes":"classes", "students":"students", "assignments":"assignments", "mistakes":"mistakes", "seats":"seats", "performances":"performances"}.get(collection)
        if not plural: return self.send_json({"error":"接口不存在"}, 404)
        data = read_data(); data[plural] = [x for x in data[plural] if x["id"] != item_id]
        if collection == "classes": data["students"] = [x for x in data["students"] if x["classId"] != item_id]
        write_data(data); return self.send_json({"ok": True})

if __name__ == "__main__":
    migrate_local_secrets()
    seed_admin()
    print("\n学生管理与统计系统已启动：http://localhost:8765\n按 Ctrl+C 可停止服务。")
    ThreadingHTTPServer(("127.0.0.1", 8765), App).serve_forever()
