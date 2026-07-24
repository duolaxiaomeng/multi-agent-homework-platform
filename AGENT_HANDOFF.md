# 学生错题管理与统计系统｜开发交接文档

## 1. 项目目标

面向中小学老师的本地错题管理 MVP。

核心体验是：老师上传**课堂内容报告**（可选）和**多张学生作业照片**，AI 自动识别课堂知识点、学生姓名和错题，生成班级维度的统计图。老师只处理低置信度或无法判断的结果。

产品原则：

- 减少老师填写、选择与重复录入；能由 AI 自动完成的，不要求老师手动选择。
- AI 不能确定时必须标记“待老师确认”，不能擅自判定。
- 数据、作业图片、厂商 API Key 均保存在本机。
- 仅接入国内模型服务。

## 2. 当前完成情况

### 已完成

- 前后端一体的本地 Web 应用。
- 班级与学生的手动管理。
- 批量上传课堂内容报告和学生作业图片。
- 通过多模态模型自动：
  - 读取课堂主题与知识点；
  - 尝试从作业中提取学生姓名；
  - 识别错题及错误类型；
  - 自动建立本次分析记录；
  - 自动新增未在名单中的学生；
  - 置信度低于 0.8、姓名无法匹配或模型标记不确定时，自动转为待确认。
- 班级总览：学生人数、错题数、待确认数量、知识点错误人数、错误类型分布、高频错题。
- 手动补充/修正错题。
- 设置页：选择模型、填写 API Key；不同厂商的 Key 分开保存。
- 已接入的国内多模态模型：
  - 阿里云百炼：Qwen3.7 Plus、Qwen3.6 Flash、Qwen3-VL Flash；
  - 月之暗面：Kimi K3；
  - 小米：MiMo V2.5。

### 未完成 / 需要重点验证

- 尚未使用真实用户 API Key 做端到端模型调用验证。
- 图片识别结果的“老师确认、批量修改、合并重复题目”交互尚不完整；当前可在“人工补充”中删除或另行录入。
- 目前一批图片统一交给模型分析，数据量大时可能触发请求体大小或模型上下文限制。
- 目前没有账号、权限、云端同步、学生端、家长端和个性化学习报告。
- API Key 以明文保存在本机 `settings.json`；MVP 可用，但正式产品应改为系统钥匙串/加密存储。
- 未做 OCR 预处理、图片压缩、旋转校正和并发/重试队列。

## 3. 项目文件与运行方式

项目目录：

```text
/Users/xiaomeng/Desktop/学生管理与统计
```

关键文件：

| 文件 | 用途 |
|---|---|
| `server.py` | Python 标准库后端、数据持久化、上传、模型适配与 AI 分析逻辑 |
| `index.html` | 单页前端页面结构 |
| `app.js` | 前端状态、页面渲染、接口调用、上传和分析流程 |
| `style.css` | 页面主样式 |
| `data.json` | 本地业务数据（运行后产生/更新） |
| `~/.student-stats/settings.json` | 模型设置和 API Key（项目外，防打包外泄；可用环境变量 STUDENT_STATS_*_KEY 覆盖） |
| `~/.student-stats/users.json` | 账号与密码哈希、令牌密钥（项目外） |
| `agent.md` | 总结 Agent 的灵魂文件（首次运行自动生成，可在“设置”页编辑） |
| `uploads/` | 已上传的作业与课堂材料图片 |
| `启动系统.command` | macOS 双击启动入口 |
| `README.md` | 面向老师的简短使用说明 |

启动：

```bash
cd '/Users/xiaomeng/Desktop/学生管理与统计'
python3 server.py
```

浏览器访问：<http://localhost:8765>

也可双击 `启动系统.command`。

当前服务约定运行在 `127.0.0.1:8765`。端口被占用时，先停止已有 `server.py` 进程，再启动。

## 4. 当前用户流程

1. 老师在“班级与学生”创建班级。学生名单可以先不完整，AI 后续会尝试自动补齐。
2. 在“设置”选择模型，并输入该厂商的 API Key。
3. 在“导入作业”第 1 步选择班级、上课日期、第几节课，上传课堂内容报告（也可只填课程名称），保存本节课——每节课只需一次。
4. 第 2 步选择刚保存的课程，分批上传学生作业照片，点击“开始 AI 错题分析”。40 名学生可分多批上传，汇总到同一节课。
5. 后端保存图片后调用模型，将结构化结果写入 `data.json`。
6. 老师回到总览，查看班级共性错误；待确认项目前显示在“人工补充”的最近记录中。

## 5. 数据结构

`data.json` 的顶级结构：

```json
{
  "classes": [],
  "students": [],
  "assignments": [],
  "mistakes": []
}
```

主要字段：

```text
classes:      id, name, grade, createdAt, ownerId, seatRows, seatCols
students:     id, name, classId
assignments:  id, title, classId, date, period, subject, content, knowledgePoints[], createdAt
mistakes:     id, studentId, assignmentId, question, knowledgePoint,
              errorType, status, note, image
seats:        id, classId, row, col, studentId   （一个班内一名学生只占一个座位）
performances: id, classId, assignmentId, row, col, studentId, tags[], note, image, updatedAt
              （按 班级+课程+座位 唯一；assignmentId 为空表示不关联课程）
```

错误类型由提示词约束为：`知识点不理解`、`计算错误`、`审题错误`、`步骤不完整`、`答案表达错误`、`未作答`、`其他`、`待老师确认`。

`settings.json` 使用以下形式：

```json
{
  "model": "qwen3.6-flash",
  "apiKeys": {
    "qwen": "...",
    "kimi": "...",
    "mimo": "..."
  }
}
```

注意：旧版系统可能存有顶层 `apiKey` 字段；`read_settings()` 已有兼容迁移逻辑。

## 6. 后端接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/data` | 读取全部业务数据 |
| GET | `/api/settings` | 返回当前模型、当前厂商 Key 是否已保存、模型列表；不会返回 Key 明文 |
| POST | `/api/settings` | 保存模型与当前厂商 API Key |
| POST | `/api/classes` | 创建班级 |
| POST | `/api/students` | 创建学生 |
| POST | `/api/mistakes` | 手动新增错题 |
| POST | `/api/upload` | 接收 Base64 图片，写入 `uploads/`，返回相对路径 |
| POST | `/api/lessons` | 创建一节课（班级+日期+节次+课堂报告/手动课程名），AI 读取课堂主题与知识点 |
| POST | `/api/seat-layout` | 设置班级座位行×列（1-12），缩小时清理超出范围的座位 |
| POST | `/api/seats` | 安排/清空某座位的学生（一个班内学生唯一） |
| POST | `/api/performances` | 按 班级+课程+座位 保存课堂表现（tags/note/workNote/做题照片），重复保存为更新 |
| GET/POST | `/api/agent/soul` | 读取/保存 Agent 灵魂文件 `agent.md`（学生无权） |
| POST | `/api/agent/chat` | Agent 会话。system 为 agent.md + 全量数据概览（班级/课程/每个学生错题与表现、有无截图）；消息提及学生姓名时自动附其做题截图（DeepSeek 等纯文本模型除外） |
| POST | `/api/agent/generate` | 生成学习总结 Markdown 并保存到指定路径；无当节课做题截图时拒绝（防编造红线） |

### 学习总结 Agent（多智能体）

- 智能体 = 一个灵魂 md 文件：通用区 `agents/*.md`（内置模板：通用、语文、算法评判，代码内置首次自动生成）；个人空间 `agents/<用户名>/*.md`（在通用基础上覆盖或自建）。同名时个人优先。
- 旧版单文件 `agent.md` 启动时自动迁移为 `agents/通用.md`。
- 灵魂文件支持可选 front matter 绑定模型：`---\nmodel: mimo-v2.5-pro\n---`，不填跟随设置中的当前模型。
- 会话/生成请求带 `agent` 字段（默认“通用”）；会话页可按智能体切换，历史互相隔离。
- 生成输入：所选学生+课程的错题记录、课堂表现（tags/note/workNote）、做题截图（须真实存在，否则拒绝——防编造红线）。
- 保存位置：请求字段 `savePath`；相对路径落到 `agent输出/` 目录，留空自动命名 `姓名-日期.md`，也支持绝对路径（本地运行，后续再做限制）。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/agents` | 当前用户可见的智能体列表（通用+我的合并） |
| GET/POST/DELETE | `/api/agents/:name` | 读/存/删智能体灵魂（写入个人空间；管理员传 `{"base":true}` 写通用区） |
| POST | `/api/agents` | 新建智能体（`{"name":"…","from":"模板名"}` 从模板复制） |
| POST | `/api/analyze` | 分析学生作业，错题挂到 `assignmentId` 指定的课程（不传则自动新建课程） |
| DELETE | `/api/classes/:id` | 删除班级及其学生 |
| DELETE | `/api/students/:id` | 删除学生 |
| DELETE | `/api/assignments/:id` | 删除分析任务 |
| DELETE | `/api/mistakes/:id` | 删除错题 |

### `/api/lessons` 请求

```json
{
  "classId": "class_xxx",
  "date": "2026-07-23",
  "period": "3",
  "reportImages": ["uploads/report.jpg"],
  "title": "不上传报告时手动填写的课程名称"
}
```

### `/api/analyze` 请求

```json
{
  "assignmentId": "assignment_xxx",
  "workImages": ["uploads/work1.jpg", "uploads/work2.jpg"]
}
```

### Agent 返回格式

服务端要求模型只返回 JSON。核心格式见 `server.py` 中的 `AGENT_PROMPT`：

```json
{
  "title": "本次作业名称",
  "lessonSummary": "课堂内容摘要",
  "knowledgePoints": ["知识点"],
  "students": [{"name": "学生姓名"}],
  "mistakes": [{
    "studentName": "学生姓名或未知学生",
    "question": "题号与简短题目",
    "knowledgePoint": "知识点或待确认",
    "errorType": "计算错误",
    "status": "confirmed",
    "confidence": 0.9,
    "note": "判断依据"
  }]
}
```

后端的二次安全规则：`confidence < 0.8`、学生无法匹配，或 `status != confirmed` 时，最终写入的错题状态一律为 `pending`。

### 登录与角色（已实现）

- `users.json` 保存账号：`{"secret": "...", "users": [{"id", "username", "realName", "role", "passwordHash", "createdAt", "lastLoginAt"}]}`；`secret` 用于签发令牌，不要外泄。
- 三种角色：`admin`（管理员）、`teacher`（老师）、`student`（学生）。首次启动自动创建内置管理员 `root` / `change-me-before-first-run`。
- 密码用 PBKDF2-HMAC-SHA256（10 万次迭代）加盐哈希；令牌为 HMAC-SHA256 签名的无状态令牌（7 天有效），前端存于 localStorage，请求头 `Authorization: Bearer <token>`。
- 数据可见范围：管理员看全部；老师看自己创建的班级（`classes.ownerId`，无 ownerId 的旧数据所有老师可见）；学生按姓名匹配名单，只读自己的错题。
- 学生账号所有写操作返回 403；账号管理接口仅管理员可用。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/auth/register` | 注册（role 为 teacher/student）并自动登录 |
| POST | `/api/auth/login` | 登录，返回 token 与用户信息 |
| GET | `/api/auth/me` | 查询当前登录用户 |
| GET | `/api/users` | 账号列表（仅管理员） |
| POST | `/api/users` | 创建账号（仅管理员） |
| POST | `/api/users/:id/password` | 重置密码（仅管理员） |
| DELETE | `/api/users/:id` | 删除账号（仅管理员；root 与当前账号不可删） |

## 7. 模型适配说明

模型配置位于 `server.py` 的 `MODELS` 字典，采用 OpenAI 兼容的 Chat Completions 数据格式，图片统一通过 `image_url` 的 Base64 data URL 传递。

| 厂商 | 模型 | 地址 | 认证头 |
|---|---|---|---|
| 阿里云百炼 | Qwen 系列 | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | `Authorization: Bearer ...` |
| Kimi | `kimi-k3` | `https://api.moonshot.ai/v1/chat/completions` | `Authorization: Bearer ...` |
| 小米 MiMo Token Plan | `mimo-v2.5-pro` / `mimo-v2.5` | `https://token-plan-cn.xiaomimimo.com/v1/chat/completions` | `Authorization: Bearer ...` |
| DeepSeek | `deepseek-v4-pro` / `deepseek-v4-flash` | `https://api.deepseek.com/v1/chat/completions` | `Authorization: Bearer ...` |

注意：小米 Token Plan（Coding Plan）的正确入口是 `token-plan-cn.xiaomimimo.com` 且用 Bearer 鉴权（早期版本误用 `api.xiaomimimo.com` + `api-key` 头，会导致连接失败）。DeepSeek V4 不支持图片输入（实测返回 `unknown variant image_url`），只能用于 Agent 会话等纯文本场景，作业/报告识别需选 Qwen、Kimi 或 MiMo。`/api/cc-switch/import` 可从本机 CC Switch 配置库（`~/.cc-switch/cc-switch.db`，只读）一键导入小米 Key；`/api/models/test` 用最小调用验证所选模型与 Key 是否可用。设置页为厂商卡片式：按厂商分组选择模型、各自保存/测试 Key，`GET /api/settings` 的 `savedKeys` 返回各厂商 Key 状态。

官方依据：

- [阿里云 Qwen 视觉模型说明](https://help.aliyun.com/en/model-studio/vision-model/)
- [Kimi 图像输入说明](https://platform.kimi.ai/docs/guide/use-kimi-vision-model)
- [小米 MiMo API 快速接入](https://platform.xiaomimimo.com/static/docs/quick-start/first-api-call.md)
- [小米 MiMo 模型能力说明](https://platform.xiaomimimo.com/static/docs/quick-start/model.md)

### 模型接入注意事项

- 切换到新厂商后，必须输入该厂商自己的 Key 一次；系统不会错误复用另一家的 Key。
- 模型 API 的实际名称与能力可能升级，优先修改 `MODELS` 字典。
- 目前没有“测试 API Key”按钮；建议下一步添加轻量健康检查接口，但要避免真实分析消耗额度。
- AI API 调用最长等待 120 秒，定义在 `http_json()`。

## 8. 推荐下一步开发顺序

### P0：先让老师能审阅 AI 结果

1. 新增“分析结果确认页”：按作业展示 AI 提取的课堂摘要、学生名单、错题。
2. 支持一键确认、编辑、删除单条错题。
3. 支持只筛选“待老师确认”。
4. 确认后再计入最终班级统计，或在统计图中区分“已确认/待确认”。

### P1：提升识别稳定性与成本控制

1. 上传前在浏览器压缩图片、纠正 EXIF 旋转。
2. 每张作业单独识别，再进行班级聚合，避免一批图片过大导致请求失败。
3. 加入任务进度、失败重试、错误提示和调用日志。
4. 依据模型输出题目指纹，合并同一道高频错题。
5. 引入 OCR 预处理，减少视觉模型成本，并把模型用于判断和分类。

### P2：数据与教学体验

1. 作业与班级关系的编辑、删除级联处理完善。
2. 单个学生趋势页（明确在首版 MVP 之后）。
3. 导出班级错题统计为 Excel/PDF。
4. 本地数据备份和恢复。

### P3：安全与正式部署

1. 将 API Key 迁移到 macOS Keychain，移除 `settings.json` 明文保存。
2. 增加账号与权限体系，严格处理未成年人数据。
3. 增加图片删除、数据保留周期与脱敏机制。
4. 改用 SQLite 或数据库，而不是完整读取/写入 JSON。

## 9. 开发注意事项

- 本项目当前无第三方 Python 包，使用 Python 标准库 HTTP server；不要为小功能贸然引入大型框架。
- 所有写文件改动使用 `apply_patch`。
- 用户的原始诉求是“减轻工作量”，新功能应优先自动化，避免增加学科、知识点、学生等强制表单字段。
- AI 结果需坚持“置信度低即待确认”的红线。
- 修改 `server.py` 后必须重启服务；前端文件修改后刷新浏览器即可。
- `data.json`、`settings.json` 与 `uploads/` 是用户数据，调试时不要随意清空、覆盖或提交。

