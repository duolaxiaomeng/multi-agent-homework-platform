"""学生信息管理底座：档案迁移、校验、批量导入解析、CSV 导出的纯逻辑。

本模块不依赖 server / HTTP，只操作普通 dict，方便自动化测试。
server.py 负责读写 data.json / users.json 后调用这里。
"""
from datetime import datetime
import csv
import io
import re

DATA_VERSION = "v0.3"

STUDENT_STATUSES = {"active": "在读", "transferred": "转出", "suspended": "休学",
                    "graduated": "毕业", "archived": "已归档"}
GENDERS = {"male": "男", "female": "女", "unknown": ""}
HISTORY_ACTIONS = {"enroll": "入学/入班", "transfer": "转班", "leave": "离班",
                   "graduate": "毕业", "restore": "恢复"}


def now_str():
    return datetime.now().isoformat(timespec="seconds")


def norm_name(name):
    return re.sub(r"\s+", "", name or "")


# ---------------------------------------------------------------- 学生档案

def student_defaults():
    return {"studentNo": "", "gender": "unknown", "enrollmentYear": "", "status": "active",
            "phone": "", "note": "", "createdAt": "", "updatedAt": "", "archivedAt": ""}


def ensure_student_fields(st):
    """补齐学生档案缺失字段（幂等）。"""
    for k, v in student_defaults().items():
        st.setdefault(k, v)
    if st.get("status") not in STUDENT_STATUSES:
        st["status"] = "active"
    if st.get("gender") not in GENDERS:
        st["gender"] = "unknown"
    return st


def validate_student_payload(data, payload, exclude_id=""):
    """校验新建/编辑学生的字段。返回清理后的 dict，失败抛 ValueError（中文）。"""
    name = (payload.get("name") or "").strip()
    if not name: raise ValueError("姓名不能为空")
    if len(name) > 30: raise ValueError("姓名过长")
    student_no = (payload.get("studentNo") or "").strip()
    if student_no:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", student_no):
            raise ValueError("学号只能包含字母、数字、- 和 _，最长 20 位")
        dup = next((s for s in data["students"] if s.get("studentNo") == student_no and s["id"] != exclude_id), None)
        if dup: raise ValueError(f"学号 {student_no} 已被学生「{dup['name']}」使用")
    gender = payload.get("gender", "unknown")
    if gender not in GENDERS: gender = "unknown"
    year = str(payload.get("enrollmentYear", "") or "").strip()
    if year and not re.fullmatch(r"\d{4}", year): raise ValueError("入学年份需为 4 位数字，如 2024")
    phone = str(payload.get("phone", "") or "").strip()
    if phone and not re.fullmatch(r"[\d+\- ]{5,20}", phone): raise ValueError("联系方式格式不正确")
    return {"name": name, "studentNo": student_no, "gender": gender,
            "enrollmentYear": year, "phone": phone, "note": str(payload.get("note", "") or "").strip()}


def active_students(data, class_id):
    """某班当前在读学生（新课次名单来源）。旧数据没有 status 字段按在读处理。"""
    return [s for s in data["students"]
            if s["classId"] == class_id and s.get("status", "active") == "active"]


# ---------------------------------------------------------------- 课次名单快照

def lesson_roster(lesson, data):
    """课次名单：优先用创建时的快照 rosterIds；旧课次没有快照时按当前班底计算（迁移会回填）。"""
    roster = lesson.get("rosterIds")
    if roster:
        by_id = {s["id"]: s for s in data["students"]}
        base = [by_id[i] for i in roster if i in by_id]
    else:
        base = [s for s in data["students"] if s["classId"] == lesson["classId"]]
    extra_ids = [x for x in lesson.get("extraStudentIds", []) if x]
    extra = [s for s in data["students"] if s["id"] in extra_ids and s["classId"] != lesson["classId"]]
    return base + extra


def snapshot_for_new_lesson(data, class_id):
    return [s["id"] for s in active_students(data, class_id)]


# ---------------------------------------------------------------- 迁移 v0.3

def migrate_v03_data(data):
    """data.json 迁移到 v0.3（幂等）：学生档案补字段、课次回填名单快照、班级补 archived。"""
    changed = False
    for st in data["students"]:
        before = dict(st)
        ensure_student_fields(st)
        if st != before: changed = True
    # 课次名单快照：当前班学生 ∪ 考勤/作业/错题里出现过的学生（保住历史关联）
    for lesson in data["lessons"]:
        if lesson.get("rosterIds"): continue
        ids = [s["id"] for s in data["students"] if s["classId"] == lesson.get("classId")]
        seen = set(ids)
        for coll, key in (("attendance", "studentId"), ("submissions", "studentId"), ("mistakes", "studentId")):
            for row in data[coll]:
                if row.get("lessonId") == lesson["id"] and row.get(key) and row[key] not in seen:
                    seen.add(row[key]); ids.append(row[key])
        lesson["rosterIds"] = ids
        changed = True
    for cls in data["classes"]:
        if "archived" not in cls:
            cls["archived"] = False; changed = True
    if data.get("version") != DATA_VERSION:
        data["version"] = DATA_VERSION; changed = True
    return changed


def migrate_users_binding(users, students):
    """users.json 迁移：学生账号补 studentId。realName 唯一匹配则自动绑定，否则留空待绑定（幂等）。"""
    changed = False
    for u in users:
        if u.get("role") != "student":
            if "studentId" not in u: u["studentId"] = ""; changed = True
            continue
        if u.get("studentId"): continue
        if "studentId" not in u:
            name = norm_name(u.get("realName") or u.get("username"))
            matches = [s for s in students if norm_name(s["name"]) == name]
            if len(matches) == 1:
                u["studentId"] = matches[0]["id"]
            else:
                u["studentId"] = ""   # 无法唯一匹配 → 待绑定
            changed = True
    return changed


def bindable_check(users, student_id, exclude_user_id=""):
    """一个学生默认只能绑定一个有效学生账号。"""
    return not any(u.get("role") == "student" and u.get("studentId") == student_id
                   and u["id"] != exclude_user_id for u in users)


# ---------------------------------------------------------------- 批量导入

_GENDER_MAP = {"男": "male", "女": "female", "male": "male", "female": "female", "m": "male", "f": "female"}


def parse_import_text(text):
    """解析粘贴文本/CSV 内容为学生行。支持：姓名 / 学号+姓名 / 学号,姓名,性别,入学年份,备注。
    返回 [{studentNo, name, gender, enrollmentYear, note, error}]，error 非空表示格式错误。"""
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("﻿")
        if not line: continue
        parts = [p.strip() for p in re.split(r"[,，\t;；]", line)]
        if len(parts) == 1:
            parts = [p for p in re.split(r"\s+", line) if p]
        row = {"studentNo": "", "name": "", "gender": "unknown", "enrollmentYear": "", "note": "", "error": ""}
        if len(parts) == 1:
            row["name"] = parts[0]
        elif len(parts) == 2:
            # 两列：优先按学号格式判断（纯数字/字母-=_），否则默认列1是学生名
            if re.fullmatch(r"[A-Za-z0-9_-]+", parts[0]) or not re.fullmatch(r"[A-Za-z0-9_-]+", parts[1]):
                row["studentNo"], row["name"] = parts
            else:
                row["name"], row["studentNo"] = parts
        else:
            row["studentNo"], row["name"] = parts[0], parts[1]
            row["gender"] = _GENDER_MAP.get(parts[2].lower(), "unknown") if parts[2] else "unknown"
            row["enrollmentYear"] = parts[3] if len(parts) > 3 else ""
            row["note"] = "，".join(p for p in parts[4:] if p) if len(parts) > 4 else ""
        if not row["name"]:
            row["error"] = "缺少姓名"
        elif row["studentNo"] and not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", row["studentNo"]):
            row["error"] = "学号格式不正确"
        elif row["enrollmentYear"] and not re.fullmatch(r"\d{4}", row["enrollmentYear"]):
            row["error"] = "入学年份需为 4 位数字"
        rows.append(row)
    return rows


def preview_import(data, class_id, rows):
    """给解析行打标记：new / duplicate（学号重复，跳过）/ conflict（同名无学号，需老师确认）/ error。"""
    by_no = {s.get("studentNo"): s for s in data["students"] if s.get("studentNo")}
    by_name = {}
    for s in data["students"]:
        by_name.setdefault(norm_name(s["name"]), []).append(s)
    seen_no, seen_name = set(), {}
    out = []
    for i, r in enumerate(rows):
        item = dict(r); item["index"] = i
        if r["error"]:
            item["status"] = "error"; item["reason"] = r["error"]
        elif r["studentNo"] and (r["studentNo"] in by_no or r["studentNo"] in seen_no):
            who = by_no.get(r["studentNo"])
            item["status"] = "duplicate"
            item["reason"] = f"学号与{'已有学生「' + who['name'] + '」' if who else '本批次前一行'}重复，将跳过"
        elif not r["studentNo"]:
            key = norm_name(r["name"])
            if key in by_name or key in seen_name:
                item["status"] = "conflict"
                item["reason"] = "系统中已存在同名学生" if key in by_name else "本批次中姓名重复"
            else:
                item["status"] = "new"; item["reason"] = ""
            seen_name[key] = True
        else:
            item["status"] = "new"; item["reason"] = ""
        if r["studentNo"]: seen_no.add(r["studentNo"])
        out.append(item)
    counts = {k: sum(1 for x in out if x["status"] == k) for k in ("new", "duplicate", "conflict", "error")}
    return out, counts


def build_import_students(data, class_id, rows, make_id):
    """确认导入：二次校验后一次性构建全部学生对象（不写数据，由调用方原子写入）。
    rows 为 preview 中老师批准的子集（含 status=new，或 conflict 且 confirmed=true）。"""
    students, errors = [], []
    batch_no = set()
    for r in rows:
        if r.get("error"): errors.append(f"第 {r.get('index', '?') + 1} 行：{r['error']}"); continue
        try:
            cleaned = validate_student_payload(data, r)
        except ValueError as exc:
            errors.append(f"「{r.get('name') or '?'}」：{exc}"); continue
        if cleaned["studentNo"] and cleaned["studentNo"] in batch_no:
            errors.append(f"学号 {cleaned['studentNo']} 在本批次中重复"); continue
        batch_no.add(cleaned["studentNo"] or f"__noid_{r.get('index')}")
        now = now_str()
        students.append({"id": make_id("student"), "classId": class_id, **cleaned,
                         "status": "active", "createdAt": now, "updatedAt": now, "archivedAt": ""})
    if errors: raise ValueError("；".join(errors))
    return students


# ---------------------------------------------------------------- CSV 导出

def students_csv(students, class_names):
    """学生名单 CSV（带 BOM，Excel 直接打开不乱码）。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["学号", "姓名", "性别", "班级", "状态", "入学年份", "联系方式", "备注"])
    for s in students:
        w.writerow([s.get("studentNo", ""), s.get("name", ""), GENDERS.get(s.get("gender"), ""),
                    class_names.get(s.get("classId"), ""), STUDENT_STATUSES.get(s.get("status"), s.get("status", "")),
                    s.get("enrollmentYear", ""), s.get("phone", ""), s.get("note", "")])
    return "﻿" + buf.getvalue()
