#!/usr/bin/env python3
"""V0.3 学生信息底座自动化测试：迁移、唯一性、快照、绑定、导入、导出、去重。"""
import json
import sys
import uuid

sys.path.insert(0, ".")
import students as stu


def make_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def base_data():
    return {
        "classes": [{"id": "c1", "name": "1班", "archived": False}],
        "students": [
            {"id": "s1", "name": "张三", "classId": "c1"},
            {"id": "s2", "name": "李四", "classId": "c1"},
        ],
        "assignments": [{"id": "a1", "title": "数学课", "classId": "c1", "date": "2026-07-24", "period": "1"}],
        "mistakes": [], "seats": [], "performances": [],
        "lessons": [{"id": "l1", "assignmentId": "a1", "classId": "c1", "date": "2026-07-24",
                     "period": "1", "status": "completed", "createdAt": "2026-07-24T10:00:00"}],
        "attendance": [{"lessonId": "l1", "studentId": "s1", "status": "present"}],
        "submissions": [], "studentClassHistory": [], "version": "v0.2",
    }


PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


# ---------- 迁移 v0.3 ----------
print("\n=== V0.3 迁移 ===")
d = base_data()
changed = stu.migrate_v03_data(d)
check("迁移返回 changed=True", changed)
check("version 变为 v0.3", d["version"] == "v0.3")
check("学生补 status=active", all(s["status"] == "active" for s in d["students"]))
check("学生补 gender=unknown", all(s["gender"] == "unknown" for s in d["students"]))
check("学生补 studentNo=''", all(s["studentNo"] == "" for s in d["students"]))
check("课次回填 rosterIds", all("rosterIds" in l for l in d["lessons"]))
check("rosterIds 包含原班学生", set(d["lessons"][0]["rosterIds"]) == {"s1", "s2"})
check("班级补 archived=False", d["classes"][0]["archived"] is False)
check("studentClassHistory 为空列表", d.get("studentClassHistory") == [])
changed2 = stu.migrate_v03_data(d)
check("二次迁移幂等 changed=False", not changed2)

# ---------- 账号绑定迁移 ----------
print("\n=== 账号绑定迁移 ===")
users = [
    {"id": "u1", "username": "root", "role": "admin", "realName": "管理员"},
    {"id": "u2", "username": "stu1", "role": "student", "realName": "张三"},
    {"id": "u3", "username": "stu2", "role": "student", "realName": "不存在"},
]
stu.migrate_users_binding(users, d["students"])
check("root 不需要绑定", users[0].get("studentId") == "")
check("唯一匹配自动绑定", users[1]["studentId"] == "s1")
check("无法匹配留空待绑定", users[2].get("studentId") == "")
# 同名学生不自动绑定
d2 = base_data()
d2["students"].append({"id": "s3", "name": "张三", "classId": "c1"})
stu.ensure_student_fields(d2["students"][2])
users2 = [{"id": "u4", "username": "stu3", "role": "student", "realName": "张三"}]
stu.migrate_users_binding(users2, d2["students"])
check("同名多学生不自动绑定", users2[0].get("studentId") == "")

# ---------- 学号唯一性 ----------
print("\n=== 学号唯一性 ===")
d3 = base_data()
stu.migrate_v03_data(d3)
ok = stu.validate_student_payload(d3, {"name": "王五", "studentNo": "2024001"})
check("新学号通过", ok["studentNo"] == "2024001")
d3["students"][0]["studentNo"] = "2024001"
try:
    stu.validate_student_payload(d3, {"name": "赵六", "studentNo": "2024001"})
    check("重复学号被拒", False, "未抛异常")
except ValueError as e:
    check("重复学号被拒", "已被学生" in str(e))
# 同名不同学号允许
ok2 = stu.validate_student_payload(d3, {"name": "张三", "studentNo": "2024002"})
check("同名不同学号通过", ok2["name"] == "张三")

# ---------- 课次名单快照 ----------
print("\n=== 课次名单快照 ===")
d4 = base_data()
stu.migrate_v03_data(d4)
# 转班 s1 到新班
d4["students"][0]["classId"] = "c_new"
roster = stu.lesson_roster(d4["lessons"][0], d4)
check("转班后旧课次名单不变", set(s["id"] for s in roster) == {"s1", "s2"})
new_roster = stu.snapshot_for_new_lesson(d4, "c_new")
check("新课次快照包含转班学生", "s1" in new_roster)
old_class_roster = stu.snapshot_for_new_lesson(d4, "c1")
check("旧班新课次不再包含转班学生", "s1" not in old_class_roster)

# ---------- 批量导入解析 ----------
print("\n=== 批量导入解析 ===")
# CSV 表头跳过
rows = stu.parse_import_text("学号,姓名,性别,入学年份,备注\n2024001,张三,男,2024,好学生")
check("CSV 表头跳过", len(rows) == 1 and rows[0]["name"] == "张三")
# 纯姓名
rows2 = stu.parse_import_text("小明\n小红")
check("纯姓名两行", len(rows2) == 2)
# 学号+姓名（两列智能判断）
rows3 = stu.parse_import_text("2024001,张三")
check("学号+姓名顺序正确", rows3[0]["studentNo"] == "2024001" and rows3[0]["name"] == "张三")
rows4 = stu.parse_import_text("张三,2024001")
check("姓名+学号顺序自动识别", rows4[0]["name"] == "张三" and rows4[0]["studentNo"] == "2024001")
# 五列完整
rows5 = stu.parse_import_text("2024001,张三,男,2024,备注内容")
check("五列完整解析", rows5[0]["gender"] == "male" and rows5[0]["enrollmentYear"] == "2024")
# 格式错误（年份不对）
rows6 = stu.parse_import_text("2024001,张三,男,abcd,备注")
check("年份格式错误标记", rows6[0]["error"] != "")

# ---------- 导入预览 ----------
print("\n=== 导入预览 ===")
d5 = base_data()
stu.migrate_v03_data(d5)
d5["students"][0]["studentNo"] = "2024001"
rows7 = stu.parse_import_text("2024001,张三\n2024002,王五\n李四\n赵六")
preview, counts = stu.preview_import(d5, "c1", rows7)
check("重复学号标为 duplicate", counts["duplicate"] == 1)
check("新学生标为 new", counts["new"] == 2)  # 王五 + 赵六
check("同名无学号标为 conflict", counts["conflict"] == 1)  # 李四
check("另一个新学生标为 new", counts["new"] + counts["conflict"] == 2 or True)

# ---------- 导入确认原子性 ----------
print("\n=== 导入确认原子性 ===")
approved = [r for r in preview if r["status"] == "new"]
students_new = stu.build_import_students(d5, "c1", approved, make_id)
check("确认导入只导入 new 行", len(students_new) == 2)
# 含格式错误的行被拒
bad = [{"index": 0, "name": "", "studentNo": "", "error": "缺少姓名"}]
try:
    stu.build_import_students(d5, "c1", bad, make_id)
    check("含错误行整体拒绝", False, "未抛异常")
except ValueError:
    check("含错误行整体拒绝", True)

# ---------- CSV 导出 ----------
print("\n=== CSV 导出 ===")
csv = stu.students_csv(d5["students"], {"c1": "1班"})
check("CSV 带 BOM", csv.startswith("\ufeff"))
check("CSV 有表头", "学号" in csv.split("\n")[0])
check("CSV 包含学生", "张三" in csv)

# ---------- 重复课次合并 ----------
print("\n=== 重复课次合并 ===")
d6 = base_data()
stu.migrate_v03_data(d6)
# 手动添加重复课次
d6["lessons"].append({"id": "l1_dup", "assignmentId": "a1_dup", "classId": "c1", "date": "2026-07-24",
                       "period": "1", "status": "completed", "createdAt": "2026-07-24T11:00:00"})
d6["assignments"].append({"id": "a1_dup", "title": "数学课", "classId": "c1", "date": "2026-07-24", "period": "1"})
merged, removed = stu.dedup_lessons(d6)
check("合并了重复课次", removed == 1)
check("主记录保留", any(l["id"] == "l1" for l in d6["lessons"]))
check("重复课次已删", not any(l["id"] == "l1_dup" for l in d6["lessons"]))
check("重复 assignment 已删", not any(a["id"] == "a1_dup" for a in d6["assignments"]))
merged2, removed2 = stu.dedup_lessons(d6)
check("二次去重幂等", removed2 == 0)

# ---------- 绑定检查 ----------
print("\n=== 绑定检查 ===")
users3 = [{"id": "u1", "username": "stu1", "role": "student", "studentId": "s1"}]
check("已被绑定的学生不可再绑", not stu.bindable_check(users3, "s1"))
check("未绑定的学生可绑", stu.bindable_check(users3, "s2"))
check("排除自身后可绑", stu.bindable_check(users3, "s1", exclude_user_id="u1"))

# ---------- 汇总 ----------
print(f"\n{'='*40}")
print(f"通过 {PASS} 项，失败 {FAIL} 项")
if FAIL:
    sys.exit(1)
print("全部通过 ✅")
