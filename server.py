#!/usr/bin/env python3
"""本地运行的学生错题管理与统计系统。"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime
import base64, hashlib, hmac, io, json, mimetypes, os, re, secrets, shutil, sqlite3, threading, time, uuid

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "data.json"
# 密钥与账号保存在项目外的用户目录，避免打包 zip 或推送 GitHub 时泄露。
CONFIG_DIR = Path.home() / ".student-stats"
CONFIG_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = CONFIG_DIR / "settings.json"
USERS_FILE = CONFIG_DIR / "users.json"
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)

DEFAULT = {"classes": [], "students": [], "assignments": [], "mistakes": [], "seats": [], "performances": [],
           "lessons": [], "attendance": [], "submissions": [], "studentClassHistory": [], "version": "v0.3"}
import students as stu
DATA_VERSION = stu.DATA_VERSION
BACKUP_FILE = ROOT / "data_v1_backup.json"
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

def _default_data():
    return {k: (v.copy() if isinstance(v, list) else v) for k, v in DEFAULT.items()}

def read_data():
    if not DATA_FILE.exists():
        return _default_data()
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        for key, default in DEFAULT.items():
            data.setdefault(key, default.copy() if isinstance(default, list) else default)
        return data
    except (json.JSONDecodeError, OSError):
        return _default_data()

_DATA_LOCK = threading.Lock()

def write_data(data):
    with _DATA_LOCK:
        DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def migrate_v02():
    """升级到 V0.2 课次语义：自动备份 → 旧 assignments 镜像为 lessons（状态 completed）→
    旧 mistakes 补 lessonId。幂等：version 已是 v0.2 直接跳过，重复启动不产生重复数据。"""
    if DATA_FILE.exists() and not BACKUP_FILE.exists():
        try:
            shutil.copy2(DATA_FILE, BACKUP_FILE)
            print(f"[学生管理系统] 已备份旧数据到 {BACKUP_FILE}")
        except OSError as exc:
            print(f"[学生管理系统] 数据备份失败：{exc}")
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else {}
    except (json.JSONDecodeError, OSError):
        raw = {}
    if raw.get("version") == DATA_VERSION and not raw.get("needs_images_backfill"): return  # 已迁移过（read_data 会自动补 version，不能用它判断）
    data = read_data()
    now = datetime.now().isoformat(timespec="seconds")
    for a in data["assignments"]:
        if any(l.get("assignmentId") == a["id"] for l in data["lessons"]): continue
        data["lessons"].append({"id": make_id("lesson"), "assignmentId": a["id"], "classId": a.get("classId", ""),
                                "date": a.get("date", ""), "period": str(a.get("period", "") or ""),
                                "status": "completed", "createdBy": "", "createdAt": a.get("createdAt", "") or now, "updatedAt": now})
    # 错题回填图片组：按 submissionId 关联该次提交的全部图片，供待确认页对照原图（幂等，每次启动都执行）。
    sub_images = {s["id"]: s.get("images", []) for s in data["submissions"]}
    lesson_by_assignment = {l["assignmentId"]: l["id"] for l in data["lessons"] if l.get("assignmentId")}
    for m in data["mistakes"]:
        m.setdefault("lessonId", lesson_by_assignment.get(m.get("assignmentId", ""), ""))
        m.setdefault("submissionId", "")
        m.setdefault("confidence", 0)
        m.setdefault("reviewedBy", "")
        m.setdefault("reviewedAt", "")
        if not m.get("images"):
            m["images"] = sub_images.get(m.get("submissionId", ""), []) or ([m["image"]] if m.get("image") else [])
    data["version"] = DATA_VERSION
    write_data(data)
    print(f"[学生管理系统] 数据已检查/迁移到 {DATA_VERSION}：课次 {len(data['lessons'])} 个")

def migrate_v03():
    """升级到 V0.3 学生信息底座：学生档案补字段、课次回填名单快照、学生账号补 studentId 绑定。幂等。"""
    data = read_data()
    if stu.migrate_v03_data(data):
        write_data(data)
        print(f"[学生管理系统] 数据已检查/迁移到 {DATA_VERSION}：学生 {len(data['students'])} 人")
    store = read_users()
    if stu.migrate_users_binding(store["users"], data["students"]):
        write_users(store)
        print("[学生管理系统] 学生账号绑定已检查/迁移")


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

def public_user(u, students=None):
    out = {"id": u["id"], "username": u["username"], "realName": u.get("realName", ""), "role": u.get("role", "teacher"),
           "lastLoginAt": u.get("lastLoginAt", ""), "studentId": u.get("studentId", "")}
    if u.get("role") == "student" and students is not None:
        st = next((s for s in students if s["id"] == u.get("studentId")), None)
        out["boundStudentName"] = st["name"] if st else ""
    return out

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

def make_user(username, password, real_name, role, student_id=""):
    return {"id": make_id("user"), "username": username, "realName": real_name, "role": role, "studentId": student_id,
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
        lessons = [l for l in data["lessons"] if l["classId"] in class_ids]
        lesson_ids = {l["id"] for l in lessons}
        attendance = [a for a in data["attendance"] if a["lessonId"] in lesson_ids]
        submissions = [s for s in data["submissions"] if s["lessonId"] in lesson_ids]
        return {"classes": classes, "students": students, "assignments": assignments, "mistakes": mistakes, "seats": seats,
                "performances": performances, "lessons": lessons, "attendance": attendance, "submissions": submissions}
    # 学生账号：只按绑定的 studentId 取数（账号创建/绑定时确定，不再按姓名动态匹配）
    sid = user.get("studentId", "")
    mine = [s for s in data["students"] if s["id"] == sid] if sid else []
    student_ids = {s["id"] for s in mine}
    mistakes = [m for m in data["mistakes"] if m.get("studentId") in student_ids]
    assignment_ids = {m["assignmentId"] for m in mistakes}
    assignments = [a for a in data["assignments"] if a["id"] in assignment_ids]
    performances = [p for p in data["performances"] if p.get("studentId") in student_ids]
    return {"classes": [], "students": mine, "assignments": assignments, "mistakes": mistakes, "seats": [],
            "performances": performances, "lessons": [], "attendance": [], "submissions": []}

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

# ---------------- V0.2 课次领域逻辑 ----------------
LESSON_STATUSES = {"in_class": "上课中", "waiting_homework": "等待作业", "analyzing": "作业分析中", "pending_review": "待确认", "completed": "已完成"}
ATTENDANCE_STATUSES = {"present": "已到", "late": "迟到", "leave": "请假", "absent": "缺勤", "early_leave": "早退"}
SUBMISSION_STATUSES = {"not_submitted": "未收取", "submitted": "已收取", "uploaded": "已上传", "analyzing": "分析中",
                       "pending_review": "待确认", "completed": "已完成", "not_required": "无需提交"}
CONFIDENCE_THRESHOLD = 0.8

WORK_ANALYZE_PROMPT = '''你是严谨的中小学作业批改助手。以下是老师指定的一名学生的作业图片（可能多张）。逐题识别：题号/题目、作答结果、是否存在错误、对应知识点、错误类型、判断依据、置信度。绝对不要猜测：无法判断正误、题目内容无法识别、知识点或错误类型不确定、疑似与其他题重复时，status 必须为 pending。只列出错误或有疑问的题目，全部正确时返回空列表。仅返回一个 JSON 对象，不要 Markdown：
{"mistakes":[{"question":"题号与简短题目","knowledgePoint":"知识点或待确认","errorType":"知识点不理解/计算错误/审题错误/步骤不完整/答案表达错误/未作答/其他/待老师确认","status":"confirmed或pending","confidence":0到1之间数字,"reason":"判断依据（需指出图中证据）"}]}'''

FEEDBACK_PROMPT = '''你是一名中小学课后反馈助手，服务对象是老师和家长。根据系统提供的真实材料生成学生本节课的反馈（Markdown）。
要求：
- 严格区分三类内容：【事实】考勤与作业提交情况；【老师记录】课堂表现原始记录（润色为家长易读的表述，先肯定再委婉建议）；【AI 判断】基于做题图片的错题分析（题号、知识点、错误类型）。
- 材料不足的部分如实写明“本节课材料不足，未做评估”，禁止编造题目、分数或表现。
- 语气对家长友好、具体，不用“差”“不行”等定性词。
结构：## 学生姓名 / #### 考勤与作业 / #### 课堂表现 / #### 作业情况 / #### 课后建议'''

def find_lesson(data, lesson_id):
    return next((l for l in data["lessons"] if l["id"] == lesson_id), None)

def lesson_in_scope(user, data, lesson):
    """课次是否在当前用户可见范围内（与 filtered_data 规则一致）。"""
    if not lesson: return False
    if user.get("role") == "admin": return True
    cls = next((c for c in data["classes"] if c["id"] == lesson["classId"]), None)
    return bool(cls) and cls.get("ownerId") in (None, "", user["id"])

def class_students(data, class_id):
    return [s for s in data["students"] if s["classId"] == class_id]

def lesson_students(data, lesson):
    """一节课的完整名单：创建时的名单快照（rosterIds）+ 调课加入的外班学生。
    学生后来转班/归档不会改变历史课次名单。"""
    return stu.lesson_roster(lesson, data)

def ensure_lesson_attendance(data, lesson, user):
    """课次创建时为名单内学生生成默认“已到”考勤（含调课加入的外班学生），老师只需改迟到/请假/缺勤/早退。
    之后加入的学生不回填历史课次。"""
    now = datetime.now().isoformat(timespec="seconds")
    existing = {a["studentId"] for a in data["attendance"] if a["lessonId"] == lesson["id"]}
    for st in lesson_students(data, lesson):
        if st["id"] in existing: continue
        data["attendance"].append({"id": make_id("attendance"), "lessonId": lesson["id"], "studentId": st["id"],
                                   "status": "present", "note": "", "updatedBy": user.get("id", ""), "updatedAt": now})

def effective_attendance(data, lesson):
    """返回 {studentId: status}：按考勤记录，缺记录默认“已到”。"""
    return {a["studentId"]: a["status"] for a in data["attendance"] if a["lessonId"] == lesson["id"]}

def lesson_pending_count(data, lesson_id):
    return sum(1 for m in data["mistakes"] if m.get("lessonId") == lesson_id and m.get("status") == "pending")

def recompute_lesson_status(data, lesson):
    """根据待确认与作业分析进度自动推进课次状态（不回退人工设置的 in_class/waiting_homework）。"""
    if lesson["status"] in ("in_class", "waiting_homework"): return
    if lesson_pending_count(data, lesson["id"]) > 0:
        lesson["status"] = "pending_review"
    else:
        subs = [s for s in data["submissions"] if s["lessonId"] == lesson["id"] and s.get("images")]
        if subs and all(s.get("status") == "completed" for s in subs):
            lesson["status"] = "completed"
    lesson["updatedAt"] = datetime.now().isoformat(timespec="seconds")

def analyze_submission(data, lesson, submission):
    """AI 分析一份学生作业（studentId 由服务端指定，AI 不参与归属判断）。返回 (新增错题, 待确认数)。"""
    if not submission.get("images"): raise ValueError("该学生还没有作业图片，无法分析")
    result = ask_agent(read_settings(), WORK_ANALYZE_PROMPT, [], submission["images"])
    # 幂等：重新分析前清掉这份作业旧的 AI 结果（老师已确认过的保留）。
    data["mistakes"] = [m for m in data["mistakes"]
                        if not (m.get("submissionId") == submission["id"] and m.get("status") in ("pending",) and not m.get("reviewedBy"))]
    saved = []
    for m in result.get("mistakes", []):
        conf = float(m.get("confidence", 0) or 0)
        pending = m.get("status") != "confirmed" or conf < CONFIDENCE_THRESHOLD or not m.get("question")
        item = {"id": make_id("mistake"), "lessonId": lesson["id"], "submissionId": submission["id"],
                "studentId": submission["studentId"], "assignmentId": lesson.get("assignmentId", ""),
                "question": m.get("question", "未能识别题目"), "knowledgePoint": m.get("knowledgePoint", "待确认"),
                "errorType": m.get("errorType", "待老师确认"), "status": "pending" if pending else "confirmed",
                "confidence": conf, "note": m.get("reason", ""), "image": submission["images"][0],
                "images": submission["images"],
                "reviewedBy": "", "reviewedAt": ""}
        data["mistakes"].append(item); saved.append(item)
    pending_n = sum(1 for m in saved if m["status"] == "pending")
    submission["status"] = "pending_review" if pending_n else "completed"
    submission["analyzedAt"] = datetime.now().isoformat(timespec="seconds")
    submission["updatedAt"] = submission["analyzedAt"]
    return saved, pending_n

def lesson_summary(data, lesson):
    """首页课次卡片摘要。名单含调课外班学生；无考勤记录默认“已到”。"""
    students = lesson_students(data, lesson)
    att_eff = effective_attendance(data, lesson)
    att_counts = {k: sum(1 for s in att_eff.values() if s == k) for k in ATTENDANCE_STATUSES}
    # 没有考勤记录的学生按默认已到计
    missing = len(students) - len(att_eff)
    if missing > 0: att_counts["present"] += missing
    subs = [s for s in data["submissions"] if s["lessonId"] == lesson["id"]]
    got = sum(1 for s in subs if s.get("images"))
    analyzed = sum(1 for s in subs if s.get("status") in ("pending_review", "completed"))
    return {"lesson": lesson, "studentCount": len(students), "attendance": att_counts,
            "homework": {"submitted": got, "total": len(students), "analyzed": analyzed},
            "pendingCount": lesson_pending_count(data, lesson["id"])}

def build_insights(data, lesson):
    """班级学情：统计只覆盖有证据的学生，缺勤/未交/未分析单独列出，不计入未掌握。无考勤记录默认“已到”。名单含调课外班学生。"""
    students = lesson_students(data, lesson)
    att = effective_attendance(data, lesson)
    subs = {s["studentId"]: s for s in data["submissions"] if s["lessonId"] == lesson["id"]}
    mistakes = [m for m in data["mistakes"] if m.get("lessonId") == lesson["id"] and m.get("status") in ("confirmed", "pending")]
    by_student = {}
    for m in mistakes: by_student.setdefault(m.get("studentId", ""), []).append(m)
    groups = {"absent": [], "not_submitted": [], "analyzing": [], "no_mistake": [], "has_mistake": [], "not_required": []}
    for st in students:
        a_st, sub = att.get(st["id"], "present"), subs.get(st["id"])
        if a_st in ("absent", "leave"): groups["absent"].append(st["name"]); continue
        if sub and sub["status"] == "not_required": groups["not_required"].append(st["name"]); continue
        if not sub or not sub.get("images"): groups["not_submitted"].append(st["name"]); continue
        if sub.get("status") not in ("pending_review", "completed"): groups["analyzing"].append(st["name"]); continue
        groups["has_mistake" if by_student.get(st["id"]) else "no_mistake"].append(st["name"])
    kp, questions, etypes = {}, {}, {}
    for m in mistakes:
        if m.get("status") != "confirmed": continue
        k, q = m.get("knowledgePoint", "待确认"), m.get("question", "未识别题目")
        kp.setdefault(k, set()).add(m.get("studentId", ""))
        questions[q] = questions.get(q, 0) + 1
        etypes[m.get("errorType", "其他")] = etypes.get(m.get("errorType", "其他"), 0) + 1
    att_counts = {k: sum(1 for s in att.values() if s == k) for k in ATTENDANCE_STATUSES}
    missing = len(students) - len(att)
    if missing > 0: att_counts["present"] += missing
    focus = sorted(((st["name"], len(by_student.get(st["id"], []))) for st in students if by_student.get(st["id"])),
                   key=lambda x: -x[1])[:5]
    return {"attendance": {"total": len(students), **att_counts},
            "homework": {"submitted": sum(1 for s in subs.values() if s.get("images")), "total": len(students),
                         "analyzed": sum(1 for s in subs.values() if s.get("status") in ("pending_review", "completed"))},
            "pendingCount": lesson_pending_count(data, lesson["id"]),
            "groups": groups,
            "knowledgePoints": [{"knowledgePoint": k, "students": len(v)} for k, v in sorted(kp.items(), key=lambda x: -len(x[1]))],
            "topQuestions": [{"question": q, "count": c} for q, c in sorted(questions.items(), key=lambda x: -x[1])[:8]],
            "errorTypes": [{"errorType": k, "count": v} for k, v in sorted(etypes.items(), key=lambda x: -x[1])],
            "focusStudents": [{"name": n, "mistakes": c} for n, c in focus]}

def build_feedback(data, lesson, student, extra=""):
    """学生个体反馈：缺勤/未交用客观模板直出；有证据时调 Agent 生成并保存 md。座位有人即判定已到。"""
    att_eff = effective_attendance(data, lesson)
    att_status = att_eff.get(student["id"], "present")
    sub = next((s for s in data["submissions"] if s["lessonId"] == lesson["id"] and s["studentId"] == student["id"]), None)
    assignment = next((a for a in data["assignments"] if a["id"] == lesson.get("assignmentId")), {})
    perfs = [p for p in data["performances"] if p.get("studentId") == student["id"] and p.get("assignmentId", "") == lesson.get("assignmentId")]
    mistakes = [m for m in data["mistakes"] if m.get("lessonId") == lesson["id"] and m.get("studentId") == student["id"]
                and m.get("status") in ("confirmed", "pending")]
    head = f"## {student['name']}\n\n课程：{lesson.get('date') or '未排期'} 第{lesson.get('period') or '?'}节《{assignment.get('title', '未命名课程')}》\n\n"
    if att_status in ("absent", "leave"):
        return head + f"#### 考勤与作业\n本节课学生{ATTENDANCE_STATUSES[att_status]}，暂无完整课堂表现和作业证据，不做学习评价。\n", None
    if not sub or not sub.get("images"):
        return head + "#### 考勤与作业\n学生本节课已到课，但尚未提交作业，暂不生成作业掌握情况。\n", None
    if sub.get("status") not in ("pending_review", "completed"):
        return head + "#### 考勤与作业\n作业已上传，分析尚未完成，请先在“课后作业”中完成分析。\n", None
    context = [f"学生：{student['name']}",
               f"课程：{lesson.get('date') or '未排期'} 第{lesson.get('period') or '?'}节《{assignment.get('title', '未命名课程')}》",
               f"课堂知识点：{'、'.join(assignment.get('knowledgePoints', [])) or '未记录'}",
               f"考勤：{ATTENDANCE_STATUSES.get(att_status, '缺勤')}"]
    for p in perfs:
        detail = "；".join(x for x in [("标签：" + "、".join(p["tags"])) if p.get("tags") else "", p.get("note", ""), ("做题情况：" + p["workNote"]) if p.get("workNote") else ""] if x)
        if detail: context.append(f"课堂表现（老师原始记录）：{detail}")
    if mistakes:
        context.append("AI 作业分析（错题）：")
        context += [f"- {m['question']}（知识点：{m.get('knowledgePoint','待确认')}，错误类型：{m.get('errorType','待老师确认')}，{'已确认' if m['status']=='confirmed' else '待确认'}，依据：{m.get('note','')}）" for m in mistakes]
    else:
        context.append("AI 作业分析：未发现错题（图片识别结果）。")
    if extra: context.append(f"老师补充：{extra}")
    content = [{"type": "text", "text": "\n".join(context) + "\n\n请根据以上材料生成学生反馈（Markdown）。"}]
    for img in sub["images"][:3]:
        try:
            raw, mime = image_part(img)
            content += [{"type": "text", "text": "作业图片："}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{raw}"}}]
        except ValueError:
            pass
    markdown = chat_model(read_settings(), FEEDBACK_PROMPT, [{"role": "user", "content": content}])
    AGENT_OUTPUT_DIR.mkdir(exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", student["name"])
    path, n = AGENT_OUTPUT_DIR / f"反馈-{safe}-{lesson.get('date') or '未排期'}.md", 1
    while path.exists():
        n += 1; path = AGENT_OUTPUT_DIR / f"反馈-{safe}-{lesson.get('date') or '未排期'}-{n}.md"
    path.write_text(markdown, encoding="utf-8")
    return markdown, {"name": path.name, "url": f"/agent输出/{path.name}"}


# ---------------- 学习总结 Agent ----------------
# Agent 的“灵魂”按智能体分别保存在 agents/ 目录（一个 md 一个智能体），老师可在“设置”页管理。
AGENT_FILE = ROOT / "agent.md"
AGENTS_DIR = ROOT / "agents"
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
- 某部分材料不足时，如实写明“本节课材料不足，未做评估”，不要虚构内容补足结构。
- 语气对家长友好、具体、不夸张，不使用“差”“不行”等定性词。
'''

CHINESE_SOUL = '''# 语文智能体灵魂设定

你是一名中小学语文老师助手，擅长作文点评、阅读分析与家长沟通。

## 你的任务
1. 根据学生作业照片或老师输入，点评作文与阅读题：先指出亮点（好词好句、结构、立意），再给出具体可操作的修改建议。
2. 把老师的课堂观察润色为家长易读、易接受的表述。
3. 需要时汇总成一篇本节课的语文学习总结（Markdown 格式）。

## 输出格式
## 学生姓名
#### 课堂表现：
#### 知识点掌握：
#### 本堂课学习内容：
#### 完成题目：
#### 课后建议：

## 红线
- 只依据提供材料写作，不编造作文内容、分数或表现；材料不足如实说明。
- 评价以鼓励为主，建议具体、对事不对人。
'''

ALGO_SOUL = '''# 算法评判智能体灵魂设定

你是一名信息学/编程课老师助手，专注评判算法题完成情况。

## 你的任务
1. 从做题截图识别每道题的题号、名称与评测状态（Accepted、部分分、Wrong Answer、编译错误等）。
2. 分析错误类型：语法问题、逻辑错误、边界遗漏、复杂度不达标；能定位到具体条件的尽量指出。
3. 给出每道未通过题的订正思路（讲方法，不直接给完整答案）。
4. 汇总成评判报告（Markdown 格式）；完成题目按 [**P1234 题目名称**](https://chuanshuo.com.cn/d/题集代码/p/P1234) 格式附链接，题集代码按班级确定（如 12 班为 CPP2026_12），不确定时用 CPP2026_03。

## 输出格式
## 学生姓名
#### 课堂表现：
#### 知识点掌握：
#### 本堂课学习内容：
#### 完成题目：
#### 课后建议：

## 红线
- 只依据截图与记录评判，看不清的题目标注“待确认”，绝不编造题号与评测结果。
'''

DEFAULT_SOULS = {"通用": DEFAULT_SOUL, "语文": CHINESE_SOUL, "算法评判": ALGO_SOUL}

def agent_dir(user):
    return AGENTS_DIR / user["username"]

def safe_agent_name(name):
    if not re.fullmatch(r"[\w\-（）() ]{1,30}", name): raise ValueError("智能体名称需为 1-30 位，可含中文、字母、数字")
    return name

def seed_agents():
    """首次运行：迁移旧的单灵魂 agent.md，并生成内置智能体模板。"""
    AGENTS_DIR.mkdir(exist_ok=True)
    if AGENT_FILE.exists():
        target = AGENTS_DIR / "通用.md"
        if not target.exists():
            try: target.write_text(AGENT_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError: pass
        try: AGENT_FILE.unlink()
        except OSError: pass
    for name, soul in DEFAULT_SOULS.items():
        f = AGENTS_DIR / f"{name}.md"
        if not f.exists():
            try: f.write_text(soul, encoding="utf-8")
            except OSError: pass

def soul_path(name, user):
    """个人空间优先，其次通用区；返回 (路径, 来源)。"""
    mine = agent_dir(user) / f"{name}.md"
    if mine.is_file(): return mine, "mine"
    base = AGENTS_DIR / f"{name}.md"
    if base.is_file(): return base, "base"
    return None, None

def list_agents(user):
    items = {f.stem: {"name": f.stem, "source": "base"} for f in sorted(AGENTS_DIR.glob("*.md")) if not f.name.startswith("._")}
    if agent_dir(user).is_dir():
        for f in sorted(agent_dir(user).glob("*.md")):
            if not f.name.startswith("._"):
                items[f.stem] = {"name": f.stem, "source": "mine"}
    return list(items.values())

def read_agent_soul(name, user):
    path, _ = soul_path(name, user)
    if not path: raise ValueError("智能体不存在")
    return path.read_text(encoding="utf-8")

def parse_soul(text):
    """解析灵魂文件可选的 front matter（--- model: xxx ---），返回 (meta, 正文)。"""
    meta = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        text = text[m.end():]
    return meta, text

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

def chat_model(settings, system_prompt, messages):
    """以 system_prompt 为“灵魂”调用所选模型；messages 为 OpenAI 兼容消息列表。"""
    choice = MODELS.get(settings["model"])
    if not choice: raise ValueError("请选择一个支持图片分析的模型")
    key = model_api_key(choice, settings)
    if not key: raise ValueError("请先在“设置”中填写 API Key")
    url, headers = model_client(choice, key)
    out = http_json(url, {"model":choice["model"], "messages":[{"role":"system", "content":system_prompt}] + messages}, headers)
    return out["choices"][0]["message"]["content"]

# ---------------- 智能导入助手（工具调用 Agent） ----------------
import import_agent

IMPORT_SESSIONS = {}          # sessionId -> session dict（内存态，服务重启即清空；草稿确认后才落库）
IMPORT_LOCK = threading.Lock()
IMPORT_SESSION_TTL = 2 * 3600  # 空闲 2 小时的会话被清理


def chat_with_tools(settings, messages, tools):
    """OpenAI 兼容的工具调用：返回完整 assistant message（可能带 tool_calls）。"""
    choice = MODELS.get(settings["model"])
    if not choice: raise ValueError("请先在“设置”中选择模型")
    key = model_api_key(choice, settings)
    if not key: raise ValueError("请先在“设置”中填写 API Key")
    url, headers = model_client(choice, key)
    payload = {"model": choice["model"], "messages": messages, "tools": tools}
    try:
        out = http_json(url, payload, headers, timeout=import_agent.STEP_TIMEOUT)
    except ValueError as exc:
        msg = str(exc)
        if "tool" in msg.lower() or "function" in msg.lower():
            msg += "（当前模型可能不支持工具调用，建议在“设置”中切换到 Kimi K3 后重试）"
        raise ValueError(msg)
    return out["choices"][0]["message"]


def _import_session(sid, user):
    with IMPORT_LOCK:
        s = IMPORT_SESSIONS.get(sid)
    if not s or s["userId"] != user["id"]: return None
    s["updatedAt"] = time.time()
    return s


def _import_emit(s, kind, text, **extra):
    with IMPORT_LOCK:
        s["events"].append({"t": datetime.now().isoformat(timespec="seconds"), "kind": kind, "text": text, **extra})
        if len(s["events"]) > 300: s["events"] = s["events"][-300:]


def _import_cleanup():
    now = time.time()
    with IMPORT_LOCK:
        for sid in [k for k, s in IMPORT_SESSIONS.items()
                    if now - s.get("updatedAt", 0) > IMPORT_SESSION_TTL and s.get("status") != "running"]:
            IMPORT_SESSIONS.pop(sid, None)


def _import_run(sid):
    """后台线程：执行一轮工具循环，事件写入会话供前端轮询。"""
    with IMPORT_LOCK:
        s = IMPORT_SESSIONS.get(sid)
    if not s: return
    try:
        user = s["user"]
        data = filtered_data(user)
        lesson = find_lesson(data, s["lessonId"]) if s.get("lessonId") else None
        system = import_agent.SOUL
        if lesson:
            cls = next((c["name"] for c in data["classes"] if c["id"] == lesson.get("classId")), "")
            system += f"\n\n本次导入目标课次：{lesson.get('date') or '未排期'} 第{lesson.get('period') or '?'}节 {cls}。所有草稿默认挂到该课次。"
        def chat(messages, tools):
            return chat_with_tools(read_settings(), [{"role": "system", "content": system}] + messages, tools)
        def image_message(path):
            raw, mime = image_part(path)
            return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{raw}"}}
        ctx = import_agent.Ctx(user=user, lesson=lesson, chat=chat, image_message=image_message,
                               data_snapshot=lambda: filtered_data(user), uploads_root=UPLOADS,
                               emit=lambda kind, text, **kw: _import_emit(s, kind, text, **kw))
        reply = import_agent.run_agent(ctx, s["messages"], s["drafts"])
        with IMPORT_LOCK:
            s["status"] = "idle"
        _import_emit(s, "message", reply)
    except Exception as exc:
        with IMPORT_LOCK:
            s["status"] = "error"
        _import_emit(s, "error", str(exc))


def _import_confirm(s, draft_index):
    """老师确认草稿 → 落库为课次错题（高置信直接确认，其余进待确认）。返回统计。"""
    with IMPORT_LOCK:
        if not (0 <= draft_index < len(s["drafts"])): raise ValueError("草稿不存在")
        draft = s["drafts"].pop(draft_index)
    data = read_data()
    lesson = find_lesson(data, draft.get("lessonId") or s.get("lessonId") or "")
    if not lesson or not lesson_in_scope(s["user"], data, lesson): raise ValueError("草稿没有有效的目标课次，无法导入")
    now = datetime.now().isoformat(timespec="seconds")
    confirmed = pending = 0
    for r in draft["rows"]:
        conf = float(r.get("confidence", 0) or 0)
        ok = bool(r.get("studentId")) and conf >= CONFIDENCE_THRESHOLD
        data["mistakes"].append({"id": make_id("mistake"), "lessonId": lesson["id"], "submissionId": "",
                                 "studentId": r.get("studentId", ""), "assignmentId": lesson.get("assignmentId", ""),
                                 "question": r["question"], "knowledgePoint": r["knowledgePoint"],
                                 "errorType": r["errorType"], "status": "confirmed" if ok else "pending",
                                 "confidence": conf, "note": (f"[智能导入·{draft['title']}] " + r.get("note", "")).strip(),
                                 "image": "", "images": [], "reviewedBy": "", "reviewedAt": "",
                                 "createdAt": now})
        confirmed, pending = confirmed + ok, pending + (not ok)
    recompute_lesson_status(data, lesson)
    write_data(data)
    return {"confirmed": confirmed, "pending": pending, "title": draft["title"]}

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

    # ---------------- 学生信息管理 API ----------------
    def _class_in_scope(self, data, class_id):
        if self.user.get("role") == "admin": return True
        cls = next((c for c in data["classes"] if c["id"] == class_id), None)
        return bool(cls) and cls.get("ownerId") in (None, "", self.user["id"])

    def _student_in_scope(self, data, st):
        return bool(st) and self._class_in_scope(data, st.get("classId", ""))

    def students_get(self, path, query):
        data = filtered_data(self.user)
        if path == "/api/students":
            out = []
            for s in data["students"]:
                cls_id, status = s.get("classId", ""), s.get("status", "active")
                if query.get("classId") and cls_id != query["classId"][0]: continue
                if query.get("status") and status != query["status"][0]: continue
                if not query.get("archived") and status == "archived": continue
                kw = (query.get("keyword", [""])[0] or "").strip().lower()
                if kw and kw not in s["name"].lower() and kw not in (s.get("studentNo", "").lower()): continue
                cls_name = next((c["name"] for c in data["classes"] if c["id"] == cls_id), "")
                out.append({**s, "className": cls_name})
            no_no = sum(1 for s in data["students"] if s.get("status", "active") == "active" and not s.get("studentNo"))
            return self.send_json({"students": out, "missingStudentNo": no_no})
        if path == "/api/students/export.csv":
            cls_id = query.get("classId", [""])[0]
            rows = [s for s in data["students"] if not cls_id or s.get("classId") == cls_id]
            names = {c["id"]: c["name"] for c in data["classes"]}
            payload = stu.students_csv(rows, names).encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=students.csv")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload)
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[1] == "students":
            st = next((s for s in data["students"] if s["id"] == parts[2]), None)
            if not st: return self.send_json({"error": "学生不存在或无权查看"}, 404)
            sid = st["id"]
            att = [a for a in data["attendance"] if a["studentId"] == sid]
            att_counts = {k: sum(1 for a in att if a["status"] == k) for k in ATTENDANCE_STATUSES}
            subs = [s for s in data["submissions"] if s["studentId"] == sid]
            mistakes = [m for m in data["mistakes"] if m.get("studentId") == sid]
            perfs = sorted([p for p in data["performances"] if p.get("studentId") == sid],
                           key=lambda p: p.get("createdAt", ""), reverse=True)[:5]
            amap = {a["id"]: a for a in data["assignments"]}
            for p in perfs:
                a = amap.get(p.get("assignmentId", ""), {})
                p["lessonLabel"] = f"{a.get('date', '')} 第{a.get('period') or '?'}节 {a.get('title', '')}".strip()
            lessons = [l for l in data["lessons"] if sid in (l.get("rosterIds") or []) or l.get("classId") == st.get("classId")]
            lessons = sorted(lessons, key=lambda l: (l.get("date", ""), l.get("period", "")), reverse=True)[:8]
            lesson_rows = [{"id": l["id"], "date": l.get("date"), "period": l.get("period"), "status": l.get("status"),
                            "statusName": LESSON_STATUSES.get(l.get("status"), l.get("status")),
                            "title": amap.get(l.get("assignmentId"), {}).get("title", "")} for l in lessons]
            safe = re.sub(r'[\\/:*?"<>|\s]+', "_", st["name"])
            feedbacks = sorted((f.name for f in AGENT_OUTPUT_DIR.glob(f"反馈-{safe}-*.md")), reverse=True)[:5] if AGENT_OUTPUT_DIR.is_dir() else []
            cls_name = next((c["name"] for c in data["classes"] if c["id"] == st.get("classId")), "")
            return self.send_json({"student": {**st, "className": cls_name},
                                   "attendance": {"total": len(att), **att_counts},
                                   "submissions": {"total": len(subs), "withImages": sum(1 for s in subs if s.get("images"))},
                                   "mistakes": {"confirmed": sum(1 for m in mistakes if m.get("status") == "confirmed"),
                                                "pending": sum(1 for m in mistakes if m.get("status") == "pending")},
                                   "performances": perfs, "lessons": lesson_rows, "feedbacks": feedbacks,
                                   "history": [h for h in data["studentClassHistory"] if h["studentId"] == sid]})
        return self.send_json({"error": "接口不存在"}, 404)

    def students_post(self, path, body):
        data = read_data()
        now = datetime.now().isoformat(timespec="seconds")
        if path == "/api/students":
            cls_id = body.get("classId", "")
            if not self._class_in_scope(data, cls_id): raise ValueError("班级不存在或无权操作")
            cleaned = stu.validate_student_payload(data, body)
            st = {"id": make_id("student"), "classId": cls_id, **cleaned,
                  "status": "active", "createdAt": now, "updatedAt": now, "archivedAt": ""}
            data["students"].append(st)
            data["studentClassHistory"].append({"id": make_id("history"), "studentId": st["id"], "fromClassId": "",
                                                "toClassId": cls_id, "action": "enroll", "reason": "",
                                                "operatedBy": self.user["id"], "operatedAt": now})
            write_data(data); return self.send_json(st, 201)
        if path == "/api/students/import-preview":
            cls_id = body.get("classId", "")
            if not self._class_in_scope(data, cls_id): raise ValueError("班级不存在或无权操作")
            text = body.get("text", "")
            fpath = body.get("path", "")
            if fpath:
                file = ROOT / fpath
                if not fpath.startswith("uploads/") or not file.is_file(): raise ValueError("文件不存在")
                if file.suffix.lower() == ".xlsx":
                    try:
                        import openpyxl
                        wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
                        text = "\n".join(",".join("" if v is None else str(v) for v in row)
                                         for row in wb.worksheets[0].iter_rows(values_only=True))
                        wb.close()
                    except ImportError: raise ValueError("服务器未安装 openpyxl，无法解析 xlsx")
                    except Exception as exc: raise ValueError(f"无法解析表格：{exc}")
                else:
                    text = file.read_text(encoding="utf-8-sig", errors="replace")
            rows = stu.parse_import_text(text)
            if not rows: raise ValueError("没有解析到任何学生行，请检查内容")
            preview, counts = stu.preview_import(data, cls_id, rows)
            return self.send_json({"rows": preview, "counts": counts})
        if path == "/api/students/import-confirm":
            cls_id = body.get("classId", "")
            if not self._class_in_scope(data, cls_id): raise ValueError("班级不存在或无权操作")
            rows = body.get("rows", [])
            if not isinstance(rows, list) or not rows: raise ValueError("没有可导入的学生")
            if len(rows) > 500: raise ValueError("单次最多导入 500 人")
            new_students = stu.build_import_students(data, cls_id, rows, make_id)  # 校验失败抛错，不会写入一半
            for st in new_students:
                data["students"].append(st)
                data["studentClassHistory"].append({"id": make_id("history"), "studentId": st["id"], "fromClassId": "",
                                                    "toClassId": cls_id, "action": "enroll", "reason": "批量导入",
                                                    "operatedBy": self.user["id"], "operatedAt": now})
            write_data(data)
            return self.send_json({"imported": len(new_students)}, 201)
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[1] == "students":
            st = next((s for s in data["students"] if s["id"] == parts[2]), None)
            if not self._student_in_scope(data, st): raise ValueError("学生不存在或无权操作")
            action = parts[3]
            if action == "update":
                # 只更新本次传入的字段（不传不覆盖）
                name = body.get("name")
                if name is not None and name.strip():
                    cleaned = stu.validate_student_payload(data, {"name": name, "studentNo": body.get("studentNo", st.get("studentNo", "")),
                        "gender": body.get("gender", st.get("gender", "unknown")), "enrollmentYear": body.get("enrollmentYear", st.get("enrollmentYear", "")),
                        "phone": body.get("phone", st.get("phone", "")), "note": body.get("note", st.get("note", ""))}, exclude_id=st["id"])
                else:
                    cleaned = stu.validate_student_payload(data, {"name": st["name"], "studentNo": body.get("studentNo", st.get("studentNo", "")),
                        "gender": body.get("gender", st.get("gender", "unknown")), "enrollmentYear": body.get("enrollmentYear", st.get("enrollmentYear", "")),
                        "phone": body.get("phone", st.get("phone", "")), "note": body.get("note", st.get("note", ""))}, exclude_id=st["id"])
                st.update(cleaned); st["updatedAt"] = now
                write_data(data); return self.send_json(st)
            if action == "transfer":
                to_id = body.get("toClassId", "")
                if to_id == st["classId"]: raise ValueError("学生已在该班级")
                if not self._class_in_scope(data, to_id): raise ValueError("目标班级不存在或无权操作")
                from_id = st["classId"]
                st["classId"] = to_id; st["status"] = "active"; st["archivedAt"] = ""; st["updatedAt"] = now
                data["studentClassHistory"].append({"id": make_id("history"), "studentId": st["id"], "fromClassId": from_id,
                                                    "toClassId": to_id, "action": "transfer",
                                                    "reason": str(body.get("reason", "")).strip(),
                                                    "operatedBy": self.user["id"], "operatedAt": now})
                write_data(data); return self.send_json(st)
            if action == "archive":
                st["status"] = "archived"; st["archivedAt"] = now; st["updatedAt"] = now
                data["studentClassHistory"].append({"id": make_id("history"), "studentId": st["id"], "fromClassId": st["classId"],
                                                    "toClassId": "", "action": "leave",
                                                    "reason": str(body.get("reason", "")).strip() or "归档",
                                                    "operatedBy": self.user["id"], "operatedAt": now})
                write_data(data); return self.send_json(st)
            if action == "restore":
                st["status"] = "active"; st["archivedAt"] = ""; st["updatedAt"] = now
                data["studentClassHistory"].append({"id": make_id("history"), "studentId": st["id"], "fromClassId": "",
                                                    "toClassId": st["classId"], "action": "restore", "reason": "",
                                                    "operatedBy": self.user["id"], "operatedAt": now})
                write_data(data); return self.send_json(st)
            if action == "status":  # 转出 / 休学 / 毕业
                status = body.get("status", "")
                if status not in ("transferred", "suspended", "graduated", "active"): raise ValueError("状态不合法")
                st["status"] = status; st["updatedAt"] = now
                st["archivedAt"] = "" if status == "active" else st.get("archivedAt", "")
                act = {"transferred": "transfer", "suspended": "leave", "graduated": "graduate", "active": "restore"}[status]
                data["studentClassHistory"].append({"id": make_id("history"), "studentId": st["id"], "fromClassId": st["classId"],
                                                    "toClassId": st["classId"] if status == "active" else "", "action": act,
                                                    "reason": str(body.get("reason", "")).strip(),
                                                    "operatedBy": self.user["id"], "operatedAt": now})
                write_data(data); return self.send_json(st)
            raise ValueError("不支持的操作")
        if len(parts) == 4 and parts[1] == "classes" and parts[3] == "archive":
            cls = next((c for c in data["classes"] if c["id"] == parts[2]), None)
            if not cls or not self._class_in_scope(data, cls["id"]): raise ValueError("班级不存在或无权操作")
            cls["archived"] = bool(body.get("archived", True))
            write_data(data)
            return self.send_json({"ok": True, "archived": cls["archived"]})
        return self.send_json({"error": "接口不存在"}, 404)

    def backup_get(self):
        """下载完整业务数据备份（zip：data.json + uploads + agent输出 + agents + meta）。
        不包含 ~/.student-stats 下的密钥与账号信息。"""
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("backup_meta.json", json.dumps({"version": DATA_VERSION, "createdAt": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False))
            if DATA_FILE.exists(): zf.write(DATA_FILE, DATA_FILE.name)
            for f in sorted(UPLOADS.rglob("*")):
                if f.is_file(): zf.write(f, str(f.relative_to(ROOT)))
            for d in (AGENT_OUTPUT_DIR, AGENTS_DIR):
                if not d.is_dir(): continue
                for f in sorted(d.rglob("*")):
                    if f.is_file(): zf.write(f, str(f.relative_to(ROOT)))
        payload = buf.getvalue(); buf.close()
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        fname = f"xuesheng-beifen-{ts}.zip"
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{fname}")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers(); self.wfile.write(payload)

    def backup_restore(self, body):
        """从备份恢复：校验格式 → 备份当前数据到 ~/.student-stats/restore-backups → 写入。"""
        if not body.get("confirm"): raise ValueError("请在请求中带上 confirm 确认恢复")
        raw = body.get("data", "")
        header, encoded = raw.split(",", 1) if "," in raw else ("", "")
        if not raw.startswith("data:application/zip") and not raw.startswith("data:application/x-zip"):
            raise ValueError("请上传 .zip 格式的备份文件")
        import zipfile
        buf = io.BytesIO(base64.b64decode(encoded))
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            if "data.json" not in names: raise ValueError("备份中没有 data.json")
            # 校验 data.json 格式
            raw_data = zf.read("data.json").decode("utf-8")
            try:
                test = json.loads(raw_data)
                for k in DEFAULT: test.setdefault(k, DEFAULT[k].copy() if isinstance(DEFAULT[k], list) else DEFAULT[k])
            except json.JSONDecodeError: raise ValueError("data.json 格式损坏")
            # 禁止路径穿越
            for name in names:
                parts = Path(name).parts
                if ".." in parts or name.startswith("/"): raise ValueError(f"备份包含非法路径：{name}")
            # 备份当前数据到恢复备份目录
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            rb = CONFIG_DIR / "restore-backups" / ts
            rb.mkdir(parents=True, exist_ok=True)
            if DATA_FILE.exists(): shutil.copy2(DATA_FILE, rb / "data.json")
            if UPLOADS.is_dir() and any(UPLOADS.iterdir()): shutil.copytree(UPLOADS, rb / "uploads", dirs_exist_ok=True)
            if AGENT_OUTPUT_DIR.is_dir(): shutil.copytree(AGENT_OUTPUT_DIR, rb / "agent输出", dirs_exist_ok=True)
            if AGENTS_DIR.is_dir(): shutil.copytree(AGENTS_DIR, rb / "agents", dirs_exist_ok=True)
            # 清空并恢复
            DATA_FILE.write_text(raw_data, encoding="utf-8")
            for d in (UPLOADS, AGENT_OUTPUT_DIR, AGENTS_DIR):
                for f in list(d.rglob("*")) if d.is_dir() else []:
                    if f.is_file(): f.unlink()
            for name in names:
                if name == "data.json": continue
                zf.extract(name, ROOT)
            buf.close()
        print(f"[学生管理系统] 备份已恢复到 {datetime.now().isoformat(timespec='seconds')}，旧数据备份在 {rb}")
        return self.send_json({"ok": True, "note": "账号和 API Key 不在备份中，需要重新配置；旧数据备份在 ~/.student-stats/restore-backups/" + ts})

    def import_agent_post(self, path, body):
        """智能导入助手会话接口：建会话 / 发消息（后台跑）/ 确认草稿 / 丢弃草稿。"""
        _import_cleanup()
        if path == "/api/import-agent/sessions":
            lesson_id = body.get("lessonId", "")
            if lesson_id:
                data = read_data()
                lesson = find_lesson(data, lesson_id)
                if not lesson or not lesson_in_scope(self.user, data, lesson):
                    return self.send_json({"error": "课次不存在或无权访问"}, 404)
            sid = make_id("import")
            with IMPORT_LOCK:
                IMPORT_SESSIONS[sid] = {"id": sid, "userId": self.user["id"], "user": self.user,
                                        "lessonId": lesson_id, "source": body.get("source", ""),
                                        "status": "idle", "events": [], "messages": [], "drafts": [],
                                        "createdAt": time.time(), "updatedAt": time.time()}
            _import_emit(IMPORT_SESSIONS[sid], "status", "会话已创建，请上传材料或直接说明需求")
            return self.send_json({"sessionId": sid}, 201)
        parts = path.strip("/").split("/")  # api / import-agent / sessions / :id / action
        if len(parts) != 5 or parts[2] != "sessions": return self.send_json({"error": "接口不存在"}, 404)
        s = _import_session(parts[3], self.user)
        if not s: return self.send_json({"error": "会话不存在或已过期，请重新打开智能导入"}, 404)
        action = parts[4]
        if action == "message":
            text = (body.get("text") or "").strip()
            files = [p for p in body.get("files", []) if isinstance(p, str) and p.startswith("uploads/") and (ROOT / p).is_file()]
            if not text and not files: raise ValueError("请输入说明或上传文件")
            with IMPORT_LOCK:
                if s["status"] == "running": raise ValueError("助手正在处理上一条消息，请稍候")
                s["status"] = "running"
            desc = text
            if files:
                desc += ("\n" if desc else "") + "老师上传了以下材料（可用对应工具读取）：\n" + "\n".join(f"- {p}" for p in files)
            with IMPORT_LOCK:
                s["messages"].append({"role": "user", "content": desc})
            _import_emit(s, "user", text or f"上传了 {len(files)} 个文件")
            threading.Thread(target=_import_run, args=(s["id"],), daemon=True).start()
            return self.send_json({"ok": True})
        if action == "confirm":
            result = _import_confirm(s, int(body.get("draftIndex", -1)))
            _import_emit(s, "status", f"草稿「{result['title']}」已导入：直接确认 {result['confirmed']} 条，进待确认 {result['pending']} 条")
            return self.send_json(result)
        if action == "discard":
            idx = int(body.get("draftIndex", -1))
            with IMPORT_LOCK:
                if not (0 <= idx < len(s["drafts"])): raise ValueError("草稿不存在")
                dropped = s["drafts"].pop(idx)
            _import_emit(s, "status", f"草稿「{dropped['title']}」已丢弃")
            return self.send_json({"ok": True})
        return self.send_json({"error": "接口不存在"}, 404)

    def do_GET(self):
        if not self.check_auth(): return
        path = urlparse(self.path).path
        if path == "/api/auth/me":
            user = self.current_user()
            if not user: return self.send_json({"error": "请先登录"}, 401)
            data = read_data()
            return self.send_json({"user": public_user(user, data["students"])})
        if path == "/api/agent/soul":
            if self.user.get("role") == "student": return self.send_json({"error": "学生账号无权查看"}, 403)
            return self.send_json({"soul": read_agent_soul("通用", self.user)})
        if path == "/api/agents":
            if self.user.get("role") == "student": return self.send_json({"error": "学生账号无权查看"}, 403)
            return self.send_json({"agents": list_agents(self.user)})
        if path.startswith("/api/agents/"):
            if self.user.get("role") == "student": return self.send_json({"error": "学生账号无权查看"}, 403)
            name = unquote(path[len("/api/agents/"):])
            try:
                return self.send_json({"name": name, "soul": read_agent_soul(name, self.user), "source": soul_path(name, self.user)[1]})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 404)
        if path.startswith("/api/import-agent/sessions/"):
            if self.user.get("role") == "student": return self.send_json({"error": "学生账号无权查看"}, 403)
            sid = path[len("/api/import-agent/sessions/"):].strip("/")
            s = _import_session(sid, self.user)
            if not s: return self.send_json({"error": "会话不存在或已过期"}, 404)
            with IMPORT_LOCK:
                return self.send_json({"status": s["status"], "events": list(s["events"]),
                                       "drafts": list(s["drafts"]), "lessonId": s.get("lessonId", "")})
        if path == "/api/lessons":
            if self.user.get("role") == "student": return self.send_json({"error": "学生账号无权查看"}, 403)
            data = filtered_data(self.user)
            cards = []
            for l in sorted(data["lessons"], key=lambda x: (x.get("date", ""), x.get("period", "")), reverse=True):
                cls = next((c for c in data["classes"] if c["id"] == l["classId"]), None)
                summary = lesson_summary(data, l)
                summary["className"] = cls["name"] if cls else "未分班"
                summary["statusName"] = LESSON_STATUSES.get(l["status"], l["status"])
                cards.append(summary)
            return self.send_json({"lessons": cards})
        if path.startswith("/api/lessons/"):
            if self.user.get("role") == "student": return self.send_json({"error": "学生账号无权查看"}, 403)
            data = read_data()
            parts = path.strip("/").split("/")  # api, lessons, id, [sub]
            lesson = find_lesson(data, parts[2] if len(parts) > 2 else "")
            if not lesson or not lesson_in_scope(self.user, data, lesson): return self.send_json({"error": "课次不存在或无权访问"}, 404)
            sub = parts[3] if len(parts) > 3 else ""
            if not sub:
                students = lesson_students(data, lesson)
                att = [a for a in data["attendance"] if a["lessonId"] == lesson["id"]]
                subs = [s for s in data["submissions"] if s["lessonId"] == lesson["id"]]
                perfs = [p for p in data["performances"] if p.get("assignmentId", "") == lesson.get("assignmentId")]
                mistakes = [m for m in data["mistakes"] if m.get("lessonId") == lesson["id"]]
                seats = [s for s in data["seats"] if s["classId"] == lesson["classId"]]
                cls = next((c for c in data["classes"] if c["id"] == lesson["classId"]), {})
                assignment = next((a for a in data["assignments"] if a["id"] == lesson.get("assignmentId")), None)
                return self.send_json({"lesson": lesson, "class": cls, "assignment": assignment, "students": students,
                                       "attendance": att, "submissions": subs, "performances": perfs,
                                       "mistakes": mistakes, "seats": seats, "pendingCount": lesson_pending_count(data, lesson["id"])})
            if sub == "attendance":
                # 座位有人即判定已到：返回应用座位覆盖后的考勤
                raw_att = [a for a in data["attendance"] if a["lessonId"] == lesson["id"]]
                overrides = seat_overrides(data, lesson)
                for a in raw_att:
                    if a["studentId"] in overrides: a["status"] = overrides[a["studentId"]]
                return self.send_json({"attendance": raw_att})
            if sub == "submissions":
                subs = {s["studentId"]: s for s in data["submissions"] if s["lessonId"] == lesson["id"]}
                rows = []
                for st in lesson_students(data, lesson):
                    s = subs.get(st["id"])
                    rows.append({"student": st, "submission": s, "status": s["status"] if s else "not_submitted",
                                 "imageCount": len(s.get("images", [])) if s else 0})
                return self.send_json({"rows": rows})
            if sub == "insights":
                return self.send_json(build_insights(data, lesson))
            return self.send_json({"error": "接口不存在"}, 404)
        if path == "/api/users":
            if not self.require_admin(): return
            store = read_users()
            data = read_data()
            return self.send_json({"users": [public_user(u, data["students"]) for u in store["users"]]})
        if path == "/api/data": return self.send_json(filtered_data(self.user))
        if path.startswith("/api/students") or path == "/api/backup" or path == "/api/auth/students":
            if path == "/api/auth/students":
                # 注册页公开学生列表（仅 id+name+className，本地工具无安全顾虑）
                data = read_data()
                names = {c["id"]: c["name"] for c in data["classes"]}
                return self.send_json({"students": [{"id": s["id"], "name": s["name"], "className": names.get(s.get("classId", ""), ""),
                    "studentNo": s.get("studentNo", "")} for s in data["students"] if s.get("status", "active") == "active"]})
            if path == "/api/backup": return self.backup_get()
            if self.user.get("role") == "student": return self.send_json({"error": "学生账号无权查看"}, 403)
            return self.students_get(path, parse_qs(urlparse(self.path).query))
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
                student_id = ""
                if role == "student":
                    # 学生自助注册：必须选择自己的学生档案，且姓名与档案一致
                    data = read_data()
                    student_id = body.get("studentId", "")
                    st = next((s for s in data["students"] if s["id"] == student_id), None)
                    if not st: raise ValueError("请选择要绑定的学生档案")
                    if stu.norm_name(st["name"]) != stu.norm_name(real_name): raise ValueError("姓名与所选学生档案不一致")
                    if not stu.bindable_check(store["users"], student_id): raise ValueError("该学生已绑定其他账号")
                user = make_user(username, password, real_name, role, student_id)
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
            if path.startswith("/api/import-agent/"):
                return self.import_agent_post(path, body)
            if path.startswith("/api/students") or (path.startswith("/api/classes/") and path.endswith("/archive")) or path == "/api/backup/restore":
                if path == "/api/backup/restore": return self.backup_restore(body)
                return self.students_post(path, body)
            data = read_data()
            if path == "/api/agent/soul":
                agent_dir(self.user).mkdir(parents=True, exist_ok=True)
                (agent_dir(self.user) / "通用.md").write_text(body.get("soul", ""), encoding="utf-8")
                return self.send_json({"ok": True})
            if path == "/api/agents":
                name = safe_agent_name(body.get("name", "").strip())
                if soul_path(name, self.user)[0]: raise ValueError("同名智能体已存在")
                src = body.get("from", "")
                text = read_agent_soul(src, self.user) if src else "# 智能体灵魂设定\n\n（在这里描述这个智能体的定位、任务与输出要求）\n"
                agent_dir(self.user).mkdir(parents=True, exist_ok=True)
                (agent_dir(self.user) / f"{name}.md").write_text(text, encoding="utf-8")
                return self.send_json({"ok": True}, 201)
            if path.startswith("/api/agents/"):
                name = safe_agent_name(unquote(path[len("/api/agents/"):]))
                if body.get("base"):
                    if not self.require_admin(): return
                    (AGENTS_DIR / f"{name}.md").write_text(body.get("soul", ""), encoding="utf-8")
                else:
                    agent_dir(self.user).mkdir(parents=True, exist_ok=True)
                    (agent_dir(self.user) / f"{name}.md").write_text(body.get("soul", ""), encoding="utf-8")
                return self.send_json({"ok": True})
            if path == "/api/agent/chat":
                messages = [{"role": m.get("role"), "content": str(m.get("content", ""))[:4000]} for m in body.get("messages", [])[-20:] if m.get("role") in ("user", "assistant")]
                if not messages: raise ValueError("请输入内容")
                soul_meta, soul_body = parse_soul(read_agent_soul(body.get("agent", "") or "通用", self.user))
                settings = read_settings()
                if soul_meta.get("model") in MODELS: settings = {**settings, "model": soul_meta["model"]}
                system = soul_body + "\n\n" + build_overview_context(data)
                student = next((s for s in data["students"] if s["id"] == body.get("studentId", "")), None)
                lesson = next((a for a in data["assignments"] if a["id"] == body.get("assignmentId", "")), None)
                if student and lesson:
                    mistakes = [m for m in data["mistakes"] if m.get("studentId") == student["id"] and m.get("assignmentId") == lesson["id"]]
                    perfs = [p for p in data["performances"] if p.get("studentId") == student["id"] and p.get("assignmentId", "") == lesson["id"]]
                    system += "\n\n当前对话选中的学生与课程详情：\n" + build_agent_context(student, lesson, mistakes, perfs, 0)
                # 消息中提到学生姓名时，收集ta的做题截图：多模态模型直接附图分析（纯文本模型除外）。
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
                soul_meta, soul_body = parse_soul(read_agent_soul(body.get("agent", "") or "通用", self.user))
                gen_settings = read_settings()
                if soul_meta.get("model") in MODELS: gen_settings = {**gen_settings, "model": soul_meta["model"]}
                markdown = chat_model(gen_settings, soul_body, [{"role": "user", "content": content}])
                save_path = resolve_save_path(body.get("savePath", ""), student, lesson)
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
                student_id = ""
                if role == "student":
                    student_id = body.get("studentId", "")
                    st = next((s for s in data["students"] if s["id"] == student_id), None)
                    if not st: raise ValueError("创建学生账号必须选择绑定的学生")
                    if not real_name: real_name = st["name"]
                    if not stu.bindable_check(store["users"], student_id): raise ValueError("该学生已绑定其他学生账号，请先解绑")
                user = make_user(username, password, real_name, role, student_id)
                store["users"].append(user); write_users(store)
                return self.send_json(public_user(user, data["students"]), 201)
            if len(parts) == 4 and parts[:2] == ["api", "users"] and parts[3] == "bind":
                if not self.require_admin(): return
                store = read_users()
                user = next((u for u in store["users"] if u["id"] == parts[2]), None)
                if not user or user.get("role") != "student": raise ValueError("学生账号不存在")
                student_id = body.get("studentId", "")
                if student_id:
                    st = next((s for s in data["students"] if s["id"] == student_id), None)
                    if not st: raise ValueError("学生不存在")
                    if not stu.bindable_check(store["users"], student_id, user["id"]):
                        raise ValueError(f"学生「{st['name']}」已绑定其他账号")
                    if not user.get("realName"): user["realName"] = st["name"]
                user["studentId"] = student_id  # 空字符串 = 解绑（待绑定）
                write_users(store)
                return self.send_json(public_user(user, data["students"]))
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
                cls = next((c for c in data["classes"] if c["id"] == class_id), None)
                if not cls: raise ValueError("班级不存在")
                if not date: date = datetime.now().strftime("%Y-%m-%d")
                # 同一日期+节次只能有一节课（一个老师同一时间不可能同时上两个班的课）
                clash = next((l for l in data["lessons"] if l.get("date", "") == date
                              and str(l.get("period", "") or "") == period), None)
                if clash:
                    clash_cls = next((c for c in data["classes"] if c["id"] == clash["classId"]), None)
                    clash_title = next((a.get("title", "") for a in data["assignments"] if a["id"] == clash.get("assignmentId")), "")
                    return self.send_json({"error": f"{date} 第{period or '?'}节已有「{clash_cls['name'] if clash_cls else '其他班'}」的《{clash_title or '未命名'}》，同一时间段不能再开课",
                                           "conflict": True, "blocking": True,
                                           "existing": {"date": date, "period": period, "className": clash_cls["name"] if clash_cls else "", "title": clash_title}}, 409)
                report_paths = body.get("reportImages", [])
                if report_paths:
                    result = ask_agent(read_settings(), REPORT_PROMPT, report_paths, [])
                    title, content, kps = result.get("title") or "课堂内容报告", result.get("lessonSummary", ""), result.get("knowledgePoints", [])
                else:
                    title, content = body.get("title", "").strip(), body.get("content", "")
                    raw_kps = body.get("knowledgePoints", "")
                    kps = [k.strip() for k in re.split(r"[、,，]", raw_kps) if k.strip()] if isinstance(raw_kps, str) else raw_kps
                    if not title: title = f"{date} 第{period or '?'}节"
                now = datetime.now().isoformat(timespec="seconds")
                assignment = {"id": make_id("assignment"), "title": title, "classId": class_id, "date": date, "period": period, "subject": "", "content": content, "knowledgePoints": kps, "createdAt": now}
                data["assignments"].append(assignment)
                lesson = {"id": make_id("lesson"), "assignmentId": assignment["id"], "classId": class_id, "date": date,
                          "period": period, "status": "in_class", "createdBy": self.user["id"], "createdAt": now, "updatedAt": now,
                          "extraStudentIds": [], "rosterIds": stu.snapshot_for_new_lesson(data, class_id)}
                data["lessons"].append(lesson)
                ensure_lesson_attendance(data, lesson, self.user)
                write_data(data); return self.send_json({**assignment, "lessonId": lesson["id"], "lesson": lesson}, 201)
            if path.startswith("/api/lessons/"):
                parts = path.strip("/").split("/")  # api, lessons, id, [sub, subId...]
                lesson = find_lesson(data, parts[2] if len(parts) > 2 else "")
                if not lesson or not lesson_in_scope(self.user, data, lesson): raise ValueError("课次不存在或无权访问")
                sub = parts[3] if len(parts) > 3 else ""
                if not sub:
                    # 修改课名 / 日期 / 节次（课名同步到关联的课堂内容）
                    changed = False
                    if body.get("date"): lesson["date"] = body["date"]; changed = True
                    if "period" in body: lesson["period"] = str(body.get("period", "")).strip(); changed = True
                    assignment = next((a for a in data["assignments"] if a["id"] == lesson.get("assignmentId")), None)
                    if "title" in body and assignment is not None:
                        assignment["title"] = body["title"].strip() or assignment["title"]; changed = True
                    if not changed: raise ValueError("没有需要修改的内容")
                    if assignment is not None:
                        assignment["date"], assignment["period"] = lesson.get("date", ""), lesson.get("period", "")
                    lesson["updatedAt"] = datetime.now().isoformat(timespec="seconds")
                    write_data(data); return self.send_json({"lesson": lesson})
                if sub == "report":
                    # 为已存在的课次补传课堂内容报告（每节课一次），AI 读取主题与知识点
                    report_paths = [p for p in body.get("reportImages", []) if isinstance(p, str) and p.startswith("uploads/") and (ROOT / p).is_file()]
                    if not report_paths: raise ValueError("请先上传课堂内容报告图片")
                    assignment = next((a for a in data["assignments"] if a["id"] == lesson.get("assignmentId")), None)
                    if not assignment:
                        assignment = {"id": make_id("assignment"), "title": "", "classId": lesson["classId"], "date": lesson.get("date", ""),
                                      "period": lesson.get("period", ""), "subject": "", "content": "", "knowledgePoints": [], "createdAt": ""}
                        data["assignments"].append(assignment); lesson["assignmentId"] = assignment["id"]
                    result = ask_agent(read_settings(), REPORT_PROMPT, report_paths, [])
                    assignment["title"] = result.get("title") or assignment.get("title") or "课堂内容报告"
                    assignment["content"] = result.get("lessonSummary", "")
                    assignment["knowledgePoints"] = result.get("knowledgePoints", [])
                    lesson["updatedAt"] = datetime.now().isoformat(timespec="seconds")
                    write_data(data); return self.send_json({"assignment": assignment, "lesson": lesson})
                if sub == "status":
                    status = body.get("status", "")
                    if status not in LESSON_STATUSES: raise ValueError("课次状态不合法")
                    lesson["status"] = status
                    lesson["updatedAt"] = datetime.now().isoformat(timespec="seconds")
                    write_data(data); return self.send_json({"lesson": lesson})
                if sub == "extra-students":
                    # 调课：把外班学生加入本节课名单，或从名单移除
                    sid = body.get("studentId", "")
                    action = body.get("action", "add")
                    st = next((s for s in data["students"] if s["id"] == sid), None)
                    if not st: raise ValueError("学生不存在")
                    if st["classId"] == lesson["classId"]: raise ValueError("该生是本班学生，无需作为调课生添加")
                    extra = lesson.setdefault("extraStudentIds", [])
                    if action == "add":
                        if sid not in extra: extra.append(sid)
                        # 为调课生补一条默认已到考勤
                        if not any(a["lessonId"] == lesson["id"] and a["studentId"] == sid for a in data["attendance"]):
                            data["attendance"].append({"id": make_id("attendance"), "lessonId": lesson["id"], "studentId": sid,
                                                       "status": "present", "note": "", "updatedBy": self.user["id"],
                                                       "updatedAt": datetime.now().isoformat(timespec="seconds")})
                    elif action == "remove":
                        lesson["extraStudentIds"] = [x for x in extra if x != sid]
                        data["attendance"] = [a for a in data["attendance"] if not (a["lessonId"] == lesson["id"] and a["studentId"] == sid)]
                    else:
                        raise ValueError("不支持的操作")
                    lesson["updatedAt"] = datetime.now().isoformat(timespec="seconds")
                    write_data(data); return self.send_json({"lesson": lesson})
                if sub == "attendance":
                    records = body.get("records", [])
                    if not isinstance(records, list): raise ValueError("考勤数据格式错误")
                    student_ids = {s["id"] for s in lesson_students(data, lesson)}
                    now = datetime.now().isoformat(timespec="seconds")
                    for rec in records:
                        sid, status = rec.get("studentId", ""), rec.get("status", "")
                        if sid not in student_ids: continue
                        if status not in ATTENDANCE_STATUSES: raise ValueError(f"考勤状态不合法：{status}")
                        row = next((a for a in data["attendance"] if a["lessonId"] == lesson["id"] and a["studentId"] == sid), None)
                        if not row:
                            row = {"id": make_id("attendance"), "lessonId": lesson["id"], "studentId": sid}
                            data["attendance"].append(row)
                        row.update({"status": status, "note": rec.get("note", row.get("note", "")), "updatedBy": self.user["id"], "updatedAt": now})
                    write_data(data)
                    return self.send_json({"attendance": [a for a in data["attendance"] if a["lessonId"] == lesson["id"]]})
                if sub == "submissions":
                    sid = body.get("studentId", "")
                    st = next((s for s in lesson_students(data, lesson) if s["id"] == sid), None)
                    if not st: raise ValueError("学生不存在或不属于这节课的名单")
                    images = [p for p in body.get("images", []) if isinstance(p, str) and p.startswith("uploads/") and (ROOT / p).is_file()]
                    sub_row = next((s for s in data["submissions"] if s["lessonId"] == lesson["id"] and s["studentId"] == sid), None)
                    now = datetime.now().isoformat(timespec="seconds")
                    if not sub_row:
                        sub_row = {"id": make_id("submission"), "lessonId": lesson["id"], "studentId": sid, "status": "not_submitted",
                                   "images": [], "createdAt": now, "updatedAt": now, "analyzedAt": ""}
                        data["submissions"].append(sub_row)
                    for p in images:
                        if p not in sub_row["images"]: sub_row["images"].append(p)
                    if body.get("status") in SUBMISSION_STATUSES: sub_row["status"] = body["status"]
                    elif images: sub_row["status"] = "uploaded"
                    sub_row["updatedAt"] = now
                    # 已完成课次收到补交：退回等待作业，分析后再推进。
                    if images and lesson["status"] == "completed":
                        lesson["status"] = "waiting_homework"; lesson["updatedAt"] = now
                    write_data(data); return self.send_json({"submission": sub_row}, 201)
                if sub == "analyze":
                    targets = [s for s in data["submissions"] if s["lessonId"] == lesson["id"] and s.get("images")
                               and s.get("status") in ("uploaded", "submitted", "analyzing")]
                    lesson["status"] = "analyzing"; lesson["updatedAt"] = datetime.now().isoformat(timespec="seconds")
                    analyzed, failed, pending_total = 0, [], 0
                    for s in targets:
                        s["status"] = "analyzing"
                        try:
                            _, pend = analyze_submission(data, lesson, s)
                            analyzed += 1; pending_total += pend
                        except Exception as exc:
                            s["status"] = "uploaded"
                            name = next((x["name"] for x in data["students"] if x["id"] == s["studentId"]), s["studentId"])
                            failed.append({"student": name, "error": str(exc)})
                    recompute_lesson_status(data, lesson)
                    write_data(data)
                    return self.send_json({"analyzed": analyzed, "pending": pending_total, "failed": failed, "lesson": lesson})
                if sub == "students" and len(parts) > 5 and parts[5] == "feedback":
                    sid = parts[4]
                    st = next((s for s in lesson_students(data, lesson) if s["id"] == sid), None)
                    if not st: raise ValueError("学生不存在或不属于这节课的名单")
                    if body.get("saveEdited"):
                        # 保存老师修订版：不覆盖 AI 原稿，另存为 -修订.md
                        text = body.get("markdown", "").strip()
                        if not text: raise ValueError("没有可保存的内容")
                        AGENT_OUTPUT_DIR.mkdir(exist_ok=True)
                        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", st["name"])
                        path, n = AGENT_OUTPUT_DIR / f"反馈-{safe}-{lesson.get('date') or '未排期'}-修订.md", 1
                        while path.exists():
                            n += 1; path = AGENT_OUTPUT_DIR / f"反馈-{safe}-{lesson.get('date') or '未排期'}-修订-{n}.md"
                        path.write_text(text, encoding="utf-8")
                        return self.send_json({"file": {"name": path.name, "url": f"/agent输出/{path.name}"}}, 201)
                    markdown, file = build_feedback(data, lesson, st, body.get("extra", "").strip())
                    return self.send_json({"markdown": markdown, "file": file}, 201)
                raise ValueError("接口不存在")
            if path.startswith("/api/submissions/"):
                parts = path.strip("/").split("/")  # api, submissions, id, images|analyze
                sub_row = next((s for s in data["submissions"] if s["id"] == (parts[2] if len(parts) > 2 else "")), None)
                lesson = find_lesson(data, sub_row["lessonId"]) if sub_row else None
                if not sub_row or not lesson_in_scope(self.user, data, lesson): raise ValueError("作业提交不存在或无权访问")
                action = parts[3] if len(parts) > 3 else ""
                if action == "images":
                    images = [p for p in body.get("images", []) if isinstance(p, str) and p.startswith("uploads/") and (ROOT / p).is_file()]
                    if not images: raise ValueError("没有可追加的图片")
                    for p in images:
                        if p not in sub_row["images"]: sub_row["images"].append(p)
                    sub_row["updatedAt"] = datetime.now().isoformat(timespec="seconds")
                    if sub_row["status"] in ("not_submitted", "submitted"): sub_row["status"] = "uploaded"
                    if lesson["status"] == "completed":
                        lesson["status"] = "waiting_homework"; lesson["updatedAt"] = sub_row["updatedAt"]
                    write_data(data); return self.send_json({"submission": sub_row})
                if action == "analyze":
                    sub_row["status"] = "analyzing"
                    lesson["status"] = "analyzing"
                    try:
                        saved, pend = analyze_submission(data, lesson, sub_row)
                    except Exception:
                        sub_row["status"] = "uploaded"; write_data(data); raise
                    recompute_lesson_status(data, lesson)
                    write_data(data)
                    return self.send_json({"mistakes": saved, "pending": pend, "submission": sub_row, "lesson": lesson})
                raise ValueError("接口不存在")
            if path == "/api/mistakes/review-all":
                lesson_id = body.get("lessonId", "")
                lesson = find_lesson(data, lesson_id)
                if not lesson or not lesson_in_scope(self.user, data, lesson): raise ValueError("课次不存在或无权访问")
                now = datetime.now().isoformat(timespec="seconds")
                n = 0
                for m in data["mistakes"]:
                    if m.get("lessonId") == lesson_id and m.get("status") == "pending":
                        m["status"] = "confirmed"; m["reviewedBy"] = self.user["id"]; m["reviewedAt"] = now; n += 1
                recompute_lesson_status(data, lesson)
                write_data(data); return self.send_json({"confirmed": n, "lesson": lesson})
            if path.startswith("/api/mistakes/") and path.endswith("/review"):
                mid = path.strip("/").split("/")[2]
                item = next((m for m in data["mistakes"] if m["id"] == mid), None)
                if not item: raise ValueError("错题不存在")
                action = body.get("action", "")
                now = datetime.now().isoformat(timespec="seconds")
                if action in ("confirm", "modify"):
                    if action == "modify":
                        for key in ("question", "knowledgePoint", "errorType", "note"):
                            if body.get(key): item[key] = body[key]
                    item["status"] = "confirmed"; item["reviewedBy"] = self.user["id"]; item["reviewedAt"] = now
                elif action == "ignore":
                    item["status"] = "ignored"; item["reviewedBy"] = self.user["id"]; item["reviewedAt"] = now
                elif action == "not_a_mistake":
                    item["status"] = "not_a_mistake"; item["reviewedBy"] = self.user["id"]; item["reviewedAt"] = now
                elif action == "merge":
                    merge_ids = [x for x in body.get("mergeIds", []) if x != mid]
                    others = [m for m in data["mistakes"] if m["id"] in merge_ids]
                    if not others: raise ValueError("请选择要合并的题目")
                    item["question"] = "；".join([item["question"]] + [o["question"] for o in others])
                    item["reviewedBy"] = self.user["id"]; item["reviewedAt"] = now
                    drop = {o["id"] for o in others}
                    data["mistakes"] = [m for m in data["mistakes"] if m["id"] not in drop]
                else:
                    raise ValueError("不支持的审核操作")
                lesson = find_lesson(data, item.get("lessonId", ""))
                if lesson: recompute_lesson_status(data, lesson)
                write_data(data); return self.send_json({"mistake": item})
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
                # 座位上有人的学生在考勤读取时自动判定为“已到”（见 effective_attendance），此处无需写考勤。
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
                # 关联课次：按 assignmentId 找到对应 lesson，保证计入课次统计与待确认。
                lesson = next((l for l in data["lessons"] if l.get("assignmentId") == item["assignmentId"]), None)
                if lesson: item["lessonId"] = lesson["id"]
                data["mistakes"].append(item)
            elif path == "/api/upload":
                raw = body.get("data", "")
                if len(raw) > 14_000_000: raise ValueError("文件超过 10MB")
                header, encoded = raw.split(",", 1) if "," in raw else ("", "")
                mime = header[5:].split(";")[0] if header.startswith("data:") else ""
                ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
                           "application/pdf": ".pdf", "text/csv": ".csv",
                           "application/vnd.ms-excel": ".xls",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx"}
                if mime not in ext_map: raise ValueError("只支持图片、PDF、Excel 或 CSV 文件")
                prefix = "import" if ext_map[mime] in (".pdf", ".csv", ".xls", ".xlsx") else "work"
                name = make_id(prefix) + ext_map[mime]
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
        if collection == "agents":
            name = safe_agent_name(unquote(item_id))
            mine = agent_dir(self.user) / f"{name}.md"
            if mine.is_file():
                mine.unlink(); return self.send_json({"ok": True})
            if self.user.get("role") == "admin" and (AGENTS_DIR / f"{name}.md").is_file():
                if name == "通用": return self.send_json({"error": "通用智能体不能删除"}, 400)
                (AGENTS_DIR / f"{name}.md").unlink(); return self.send_json({"ok": True})
            return self.send_json({"error": "智能体不存在或无权删除"}, 404)
        if collection == "lessons":
            data = read_data()
            lesson = find_lesson(data, item_id)
            if not lesson or not lesson_in_scope(self.user, data, lesson):
                return self.send_json({"error": "课次不存在或无权访问"}, 404)
            lid = lesson["id"]
            data["lessons"] = [l for l in data["lessons"] if l["id"] != lid]
            data["attendance"] = [a for a in data["attendance"] if a.get("lessonId") != lid]
            data["submissions"] = [s for s in data["submissions"] if s.get("lessonId") != lid]
            data["mistakes"] = [m for m in data["mistakes"] if m.get("lessonId") != lid]
            # 连带删除关联的课堂内容，避免总览/今日课表出现幽灵课程
            if lesson.get("assignmentId"):
                data["assignments"] = [a for a in data["assignments"] if a["id"] != lesson["assignmentId"]]
                data["performances"] = [p for p in data["performances"] if p.get("assignmentId") != lesson["assignmentId"]]
            write_data(data); return self.send_json({"ok": True})
        if collection == "classes":
            data = read_data()
            cls = next((c for c in data["classes"] if c["id"] == item_id), None)
            if not cls: return self.send_json({"error": "班级不存在"}, 404)
            active = [s for s in data["students"] if s["classId"] == item_id and s.get("status", "active") == "active"]
            has_lessons = any(l["classId"] == item_id for l in data["lessons"])
            if active: raise ValueError(f"班级里还有 {len(active)} 名在读学生，请先转班或归档后再删除")
            if has_lessons: raise ValueError("该班级有历史课次，建议归档而非删除（在班级管理中点归档即可）")
            data["classes"].remove(cls); write_data(data); return self.send_json({"ok": True})
        plural = {"students":"students", "assignments":"assignments", "mistakes":"mistakes", "seats":"seats", "performances":"performances"}.get(collection)
        if not plural: return self.send_json({"error":"接口不存在"}, 404)
        data = read_data(); data[plural] = [x for x in data[plural] if x["id"] != item_id]
        write_data(data); return self.send_json({"ok": True})

if __name__ == "__main__":
    migrate_local_secrets()
    seed_admin()
    seed_agents()
    migrate_v02()
    migrate_v03()
    host = os.environ.get("STATS_HOST", "127.0.0.1")
    port = int(os.environ.get("STATS_PORT", "8765"))
    print(f"\n学生管理与统计系统已启动：http://{host}:{port}\n按 Ctrl+C 可停止服务。")
    ThreadingHTTPServer((host, port), App).serve_forever()
