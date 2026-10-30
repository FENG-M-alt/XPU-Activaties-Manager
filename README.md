# 劳动活动签到系统 · 后端 API

依据《劳动活动签到系统 后端 API 接口文档 V1.0》（LAB-CHECKIN-API-001）实现的完整后端服务：
**文档中 U-01 ~ U-17、A-01 ~ A-36 共 53 个接口全部落地**，另补充 14 个配套/扩展接口（OpenAPI 共 74 个操作）。

| 项目 | 说明 |
| --- | --- |
| 技术栈 | Python 3.13 · FastAPI 0.162 · SQLAlchemy 2.1 · Pydantic 2 · PyJWT · openpyxl |
| 接口前缀 | `/api/v1`，管理端统一 `/api/v1/admin` |
| 数据库 | 默认 SQLite（零配置），通过 `DATABASE_URL` 可切换 MySQL / PostgreSQL |
| 文档 | 启动后访问 <http://127.0.0.1:8000/docs>（OpenAPI 3.0，74 个操作） |
| 自测 | `python tests/smoke_test.py` —— **201 项断言全部通过** |

---

## 1. 快速开始

虚拟环境已创建（`.venv`），依赖已安装。直接启动：

```powershell
# 方式一：开发模式（自动重载）
.\.venv\Scripts\python.exe run.py

# 方式二：uvicorn 直接启动
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

首次启动会**自动建表并写入示例数据**（`AUTO_SEED=true`）：

- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- 枚举字典：<http://127.0.0.1:8000/api/v1/enums>

### 示例账号

| 角色 | 账号 | 密码 | 说明 |
| --- | --- | --- | --- |
| 超级管理员 | `superadmin` | `Admin@123456` | 全校数据范围 |
| 学院管理员 | `csadmin` | `Admin@123456` | 数据范围 COLLEGE，计算机学院 |
| 学生 | `2024010101` | `Student@2026` | 首次登录需改密（`is_first_login=true`） |
| 学生 | 其余 11 个示例学号 | `Student@2026` | 见 `app/seed.py` |

> 生产部署前务必修改 `JWT_SECRET`、关闭 `AUTO_SEED`、删除示例账号。

### 重新初始化

```powershell
Remove-Item lab_checkin.db          # 删除开发库
python -m app.seed                  # 重新建表 + 示例数据
```

---

## 2. 目录结构

```
app/
├── main.py                  # 应用装配：中间件、异常处理、静态资源、后台状态任务
├── config.py                # 全部配置（环境变量 / .env 覆盖）
├── database.py              # 引擎、会话、SQLite PRAGMA(WAL/外键)
├── seed.py                  # 示例数据（幂等）
├── core/
│   ├── errors.py            # 业务码 + BizError + HTTP 映射（文档第 8 章）
│   ├── responses.py         # 统一信封 ok() / 分页
│   ├── context.py           # trace_id 上下文
│   ├── security.py          # PBKDF2 密码、JWT、验证码、脱敏、恒定时间比较
│   ├── deps.py              # 认证依赖、数据范围过滤
│   ├── enums.py             # 文档 6.1 全部枚举
│   ├── guards.py            # 限流、幂等
│   ├── timeutil.py          # 东八区时间、学期/周/月区间
│   ├── sanitize.py          # 富文本白名单过滤
│   └── files.py             # 下载响应、路径穿越防护
├── models/                  # 15 张表（学生/管理员/活动/报名/公告/会话/日志/字典/参数/导入导出…）
├── schemas/
│   ├── requests.py          # 请求体校验模型
│   └── serializers.py       # 输出 DTO（时间统一 ISO8601+08:00）
├── services/
│   ├── activity.py          # 状态机、签到码/分享链接
│   ├── registration.py      # 名额占用、报名、审核
│   ├── checkin.py           # 8 步签到校验、锁定、补签/撤销
│   ├── stats.py             # 4 类统计聚合
│   ├── auth.py              # 会话、登录锁定、验证码、权限点
│   ├── importer.py          # Excel 解析/预览/确认写入
│   ├── exporter.py          # Excel 生成
│   └── logs.py              # 操作日志
└── api/v1/
    ├── auth.py  me.py  home.py  activities.py  registrations.py  notices.py  share.py
    └── admin/  auth.py dashboard.py activities.py registrations.py
                students.py stats.py exports.py notices.py admin_accounts.py uploads.py
tests/smoke_test.py          # 端到端冒烟测试（独立 test_smoke.db）
```

---

## 3. 接口总览

### 3.1 学生端（`/api/v1`）

| 编号 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| U-01 | POST | `/auth/login` | 学生登录（refresh token 写入 HttpOnly Cookie） |
| U-02 | POST | `/auth/refresh` | 刷新令牌（凭 Cookie） |
| U-03 | POST | `/auth/logout` | 退出登录（同时作废 access 与 refresh） |
| U-04 | POST | `/auth/password` | 修改密码（成功后注销其他会话） |
| U-05 | GET | `/me` | 个人信息（手机号脱敏） |
| U-06 | PATCH | `/me` | 仅允许修改手机号 |
| U-07 | GET | `/home` | 首页聚合（未登录 todo 为 0） |
| U-08 | GET | `/activities` | 活动列表（关键字/类型/时间/可报名/排序/分页） |
| U-09 | GET | `/activities/{id}` | 活动详情 + `my_registration` + `signup_state` |
| U-10 | GET | `/share/{token}` | 分享页（免登录，不含签到码/名单） |
| U-11 | POST | `/activities/{id}/registrations` | 提交报名（5 秒幂等） |
| U-12 | DELETE | `/registrations/{id}` | 取消报名（立即释放名额） |
| U-13 | GET | `/me/registrations` | 我的报名（状态 + 学期筛选） |
| U-14 | GET | `/registrations/{id}` | 报名详情（越权 40300） |
| U-15 | POST | `/activities/{id}/checkin` | 提交签到码（8 步校验 + 锁定） |
| U-16 | GET | `/me/stats` | 个人统计（概览/类型分布/月度趋势/明细） |
| U-17 | GET | `/notices` | 公告列表（到期自动下线） |

### 3.2 管理端（`/api/v1/admin`）

| 编号 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| A-01 | POST | `/auth/login` | 管理员登录（图形验证码 + 失败锁定） |
| A-02 | GET | `/me` | 当前管理员 + 权限点列表 |
| A-03 | GET | `/dashboard` | 工作台指标、待办、30 天趋势 |
| A-04 | GET | `/activities` | 活动列表（多条件 + 数据范围） |
| A-05 | POST | `/activities` | 新建活动（草稿） |
| A-06 | GET | `/activities/{id}` | 活动详情（含签到码、分享链接、统计） |
| A-07 | PATCH | `/activities/{id}` | 编辑活动（乐观锁 `version`） |
| A-08 | POST | `/activities/{id}/publish` | 发布（生成唯一签到码与分享 token） |
| A-09 | POST | `/activities/{id}/copy` | 复制为草稿 |
| A-10 | POST | `/activities/{id}/cancel` | 取消活动（原因 ≥ 5 字，级联取消报名） |
| A-11 | POST | `/activities/{id}/finish` | 提前结束（未签到 → ABSENT） |
| A-12 | DELETE | `/activities/{id}` | 删除（仅草稿/已取消且无签到，软删除） |
| A-13 | POST | `/activities/{id}/reset-code` | 重置签到码（旧码 → 40910） |
| A-14 | GET | `/activities/{id}/registrations` | 报名名单 |
| A-15 | POST | `/registrations/{id}/approve` | 审核通过（校验名额） |
| A-16 | POST | `/registrations/{id}/reject` | 审核拒绝（理由必填） |
| A-17 | POST | `/activities/{id}/batch-audit` | 批量审核（≤ 200，部分失败不影响其他） |
| A-18 | POST | `/registrations/{id}/checkin` | 管理员补签（MANUAL，记录操作人） |
| A-19 | DELETE | `/registrations/{id}/checkin` | 撤销签到（仅超管） |
| A-20 | GET | `/activities/{id}/checkin-board` | 签到看板（学号脱敏，30 秒轮询） |
| A-21 | GET | `/activities/{id}/export/registrations` | 导出签到表（> 1 万行转 202 异步） |
| A-22 | GET | `/students` | 学生列表（手机号脱敏 + 累计参与/学时） |
| A-23 | POST | `/students/import` | 上传 Excel 解析预览（30 分钟有效） |
| A-24 | POST | `/students/import/{batchId}/confirm` | 确认写入（skip / update） |
| A-25 | GET | `/students/import/batches` | 导入批次与失败明细 |
| A-26 | PATCH | `/students/{id}` | 停用/启用、重置密码 |
| A-27 | GET | `/students/{id}/records` | 学生参与记录 |
| A-28 | GET | `/stats/overview` | 数据总览（趋势/类型分布/学院排行） |
| A-29 | GET | `/stats/activities` | 活动维度统计 |
| A-30 | GET | `/stats/students` | 学生维度统计 |
| A-31 | GET | `/stats/colleges` | 学院维度统计 |
| A-32 | GET | `/exports/{id}` | 下载异步导出文件（支持令牌，7 天有效） |
| A-33 | GET | `/dicts` | 字典查询（写操作仅超管） |
| A-34 | GET/PATCH | `/settings` | 系统参数查询与更新（记录前后值） |
| A-35 | GET | `/logs`、`/logs/export` | 操作日志查询与导出（普通管理员仅本人） |
| A-36 | GET/POST/PATCH | `/admins` | 管理员账号管理（超管） |

### 3.3 扩展接口（文档未列，前端联调必需）

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/admin/auth/captcha` | 图形验证码（SVG data URL，5 分钟、一次性） |
| POST | `/admin/auth/refresh` · `/admin/auth/logout` · `/admin/auth/password` | 管理端会话 |
| GET/POST/PATCH/DELETE | `/admin/notices` | 公告维护（学生端 U-17 的数据来源） |
| POST | `/admin/uploads/cover` | 活动封面上传（JPG/PNG ≤ 2MB，校验文件头） |
| GET | `/api/v1/uploads/{file}` | 封面静态访问 |
| GET | `/admin/exports` | 导出任务列表（下载中心） |
| GET | `/admin/students/import/{batchId}/passwords` | 初始密码清单（**仅可下载一次**） |
| GET | `/auth/check` | 前端探测会话是否有效 |
| GET | `/api/v1/enums` · `/api/v1/health` | 枚举字典、健康检查 |

---

## 4. 通用约定的落地

**统一信封**（文档 2.3）——所有 JSON 接口返回 `{code, message, data, trace_id}`；`trace_id` 同时回写 `X-Trace-Id` 响应头，并与后端日志一致。文件下载按文档返回文件流（例外）。

**分页**（2.4）——`page` 从 1 开始；学生端默认 10、管理端默认 20；`page_size > 100` 按 100 处理并在 `message` 中提示；超出页范围返回空 `items` 而非错误。

**幂等**（2.5）——报名与签到按 `user_id + path + body` 计算幂等键，5 秒内重复请求直接返回首次结果；活动编辑用 `version` 乐观锁，冲突返回 `40904`。

**限流**（2.6）——登录 10 次/分/IP、签到 10 次/分/用户、导出 5 次/小时/账号、其他写接口 30 次/分/用户，超限 `42900`。

**时间**（2.1/7.5）——数据库存东八区本地时间，输出统一 `2026-10-21T14:30:00+08:00`；所有判断以服务端时间为准。

**错误码**（第 8 章）——`40001/40100/40101/40102/40103/40300/40400/40901~40910/42900/50000` 全部实现并映射到对应 HTTP 状态；参数错误在 `data` 中给出 `{field, reason}`。

---

## 5. 关键业务规则的实现方式

**名额不超卖** —— `UPDATE activities SET registered_count = registered_count + 1 WHERE id = ? AND registered_count < capacity` 原子扣减，`rowcount = 0` 即返回 `40901`；取消/拒绝时原子回退，并支持 `recount_registered()` 修正计数漂移。待审核记录在提交时即预占名额（文档 7.1.2）。

**状态机与惰性校正** —— `effective_status()` 按当前时间推算展示状态；读接口触发 `sync_activity()` 落库校正，另有后台任务每 60 秒批量推进（`ENABLE_STATE_TASK`）。活动结束自动把 `APPROVED` 置 `ABSENT`；活动开始自动把 `PENDING` 置 `REJECTED`（理由"活动已开始，未完成审核"）。

**签到 8 步校验**（文档 4.9 顺序）——登录 → 活动未取消/未结束 `40904` → 无报名 `40400` → 状态非已通过 `40907`（已签到直接 `40908`）→ 不在签到窗 `40906` → 码不匹配 `40905`、命中历史旧码 `40910` → 重复签到 `40908` → 连续输错 5 次锁定 10 分钟 `40909`。签到码用 `hmac.compare_digest` 恒定时间比较；每次尝试落 `checkin_attempts` 表。

**数据范围**（2.7）——查询层过滤：`ALL` 全校、`COLLEGE` 本学院（`activity.college` 或本人创建）、`SELF` 仅本人创建；越权返回 `40300` 且不返回任何数据。学生端只能访问本人资源。

**导入**（2.8 / A-23~A-25）——扩展名 + 文件头（`PK\x03\x04`）双重校验，≤ 10MB、≤ 5000 行，逐行校验学号格式/文件内重复/手机号；预览结果存 `import_batches` 保留 30 分钟，确认写入返回 `imported/updated/skipped/failed`，初始密码清单仅可下载一次后即清除。

**导出**（A-21/A-32）——同步生成 `.xlsx`（`活动名称_签到表_YYYYMMDD.xlsx`），超过 1 万行改返回 202 + `task_id` + 带时效下载令牌；文件保留 7 天，过期 `40400`。

---

## 6. 与文档的差异与补充（建议评审确认）

1. **`Activity.college` 字段**：文档 6.2 未列该字段，但 2.7 的 `COLLEGE` 数据范围需要活动归属学院。实现为可选字段，创建时若管理员本身是学院范围则自动继承。
2. **签到率口径**：统一为 `签到人次 / (签到人次 + 缺席人次)`，分母为 0 返回 `—`（符合 6.5 的格式要求，避免未开始活动拉低比率）。
3. **迟到判定**：按 4.9 字面"晚于活动开始时间"，并增加系统参数 `checkin_late_grace_minutes`（默认 0）便于现场弹性调整。
4. **`DELETE` 携带原因**：`A-10`/`A-12`/`A-19` 的 `reason` 同时支持请求体与查询参数，避免部分客户端无法发送 DELETE body。
5. **管理员独立会话**：管理端登录/刷新/退出使用 `/admin/auth/*`（文档 3.6 仅定义登录），刷新令牌共用同一 Cookie 机制。
6. **初始密码清单**：A-24 返回中给出 `password_count` 与一次性下载地址；重置密码（A-26）直接在响应中返回 `initial_password`（仅一次）。
7. **内存实现**：限流、幂等、验证码为进程内 TTL 存储，单实例足够；多 worker/多实例部署需替换为 Redis（`app/core/cache.py` 与 `guards.py` 已隔离）。
8. **未接入统一身份认证**：对应文档 10.2 待确认事项，OAuth2/CAS 扩展点保留在 `services/auth.py`。

---

## 7. 测试

```powershell
.\.venv\Scripts\python.exe tests\smoke_test.py
```

- 使用独立库 `test_smoke.db`，不污染开发数据；覆盖 53 个文档接口 + 关键错误码 + 数据范围 + 幂等/锁定/名额/旧码失效等边界。
- 当前结果：**201 项断言全部通过**。

数据库表：`students`、`admins`、`activity_types`、`activities`、`activity_checkin_codes`、`registrations`、`notices`、`auth_sessions`、`login_attempts`、`checkin_attempts`、`operation_logs`、`dict_items`、`system_settings`、`import_batches`、`export_tasks`。

---

## 8. 生产部署清单

1. `.env` 中设置强随机 `JWT_SECRET`，`AUTO_SEED=false`，`DEBUG=false`。
2. `DATABASE_URL` 切换 MySQL/PostgreSQL；用 Alembic（或自建迁移）替代 `create_all`。
3. `COOKIE_SECURE=true`（HTTPS 下必须），`COOKIE_SAMESITE=lax`，`CORS_ORIGINS` 改为具体前端域名。
4. 多 worker 时把限流/幂等/验证码迁移到 Redis；`ENABLE_STATE_TASK` 仅在一个实例开启，或改用外部调度。
5. 反向代理透传 `X-Forwarded-For`（审计与限流依赖 IP）。
6. 导出目录 `storage/exports`、封面 `storage/covers` 挂载持久卷并配置清理策略。
