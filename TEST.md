# 测试指南（TEST.md）

> 数据库已按《数据库设计说明书 V1.0》建好并写入演示数据，**开箱即可测**。
> 本文档给出：环境信息 → 预置账号与数据 → 测试路线 → 错误码触发方法 → 数据重置。

---

## 一、环境现状

### 1.1 数据库

| 项 | 值 |
| --- | --- |
| 实例 | 本机 MySQL **8.0.45**，`127.0.0.1:3306` |
| 库名 | **`lab_checkin`**（`utf8mb4` / `utf8mb4_0900_ai_ci`） |
| 建库脚本 | `sql/01_schema_core.sql`、`sql/02_schema_supplement.sql`、`sql/03_init_data.sql`、`sql/04_demo_data.sql` |
| 应用账号 | `lab_checkin` / `Lab_Checkin_2026`（仅 SELECT / INSERT / UPDATE / DELETE，无 DDL 权限） |
| 管理账号 | `root` / 你的原密码（建表、改表、清库用） |

> 你原有的 `xpuactivatiesmanagertest`、`music_platform`、`myblog` 等库**未做任何改动**。

已建 12 张表与数据量：

| 表 | 行数 | 表 | 行数 |
| --- | ---: | --- | ---: |
| `users` | 12 | `activities` | 7 |
| `admins` | 3 | `registrations` | 24 |
| `checkin_logs` | 13 | `notices` | 3 |
| `operation_logs` | 6 | `dict_items` | 11 |
| `settings` | 11 | `auth_sessions` | 0（登录后产生） |
| `export_tasks` | 0（导出后产生） | `import_batches` | 0（导入后产生） |

### 1.2 应用配置

根目录已有 `.env`（已被 `.gitignore` 忽略），指向上述库、`AUTO_CREATE_TABLES=false`，JWT 密钥已随机生成：

```ini
DATABASE_URL=mysql+pymysql://lab_checkin:Lab_Checkin_2026@127.0.0.1:3306/lab_checkin?charset=utf8mb4
AUTO_CREATE_TABLES=false
```

> 说明：`app/seed.py` 已按你的要求删除，应用启动**不再写入任何示例数据**，数据完全由 `sql/04_demo_data.sql` 预置。

### 1.3 启动

```powershell
# 项目根目录
.\.venv\Scripts\python.exe run.py
# 或
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- 接口文档（可直接点 **Try it out**）：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- 若启动报连接错误，先用 §1.1 的 `lab_checkin` 账号 `SELECT 1` 验证密码与授权。

---

## 二、预置账号

| 角色 | 账号 | 密码 | 备注 |
| --- | --- | --- | --- |
| 超级管理员 | `superadmin` | `Admin@123456` | `data_scope=ALL`，全校数据 |
| 学院管理员 | `csadmin` | `Admin@123456` | `COLLEGE`，仅计算机学院 |
| 普通管理员 | `teacher01` | `Admin@123456` | `SELF`，仅本人创建的活动 |
| 学生 | `2024010101` | `Student@2026` | **首次登录需改密**（`is_first_login=1`） |
| 学生 | `2024010102` ~ `2024040102` | `Student@2026` | 共 10 个可用 |
| 已停用学生 | `2023010102` | `Student@2026` | `DISABLED`，用于测 40102 |

> 管理员登录必须带图形验证码，获取方法见 §5.1。

---

## 三、预置活动（关键测试载体）

| # | 活动 | 状态 | 已占/上限 | 签到码 | 分享 token | 可用于验证 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 图书馆志愿整理服务 | `ONGOING` | 8/30 | **`382915`** | `Ab3kL9mN2pQr` | **签到成功流程**、签到看板、补签 |
| 2 | 敬老院慰问演出 | `REGISTERING` | 4/20 | `741028` | `Zx7vB4nM1qWs` | 需审核：通过/拒绝/批量审核 |
| 3 | 校园环保宣传周 | `REGISTERING` | **2/2** | `506173` | `Qm8tR2vY6kLp` | **名额已满 40901** |
| 4 | 校园环境清洁行动 | `FINISHED` | 8/40 | `194827` | `Hd5sK1wC9nTz` | 已完成/缺席统计、结束态 |
| 5 | 劳动教育专题讲座（草稿） | `DRAFT` | 0/100 | — | — | 草稿不出现在学生列表、可发布 |
| 6 | 秋季运动会志愿服务 | `PUBLISHED` | 0/50 | `628401` | `Vb2nM7xL4qRz` | **报名未开始 40903**、`NOT_STARTED` |
| 7 | 旧衣物捐赠活动 | `CANCELLED` | 0/30 | `773905` | `Kp6yT3nW8mVs` | **活动已取消 40904**、分享页提示 |

**活动 1 的签到窗口当前是开放的**（窗口 = 开始前 60 分钟 ~ 结束后 60 分钟），可直接测签到。

可立即用来签到的学生（已通过审核、尚未签到，签到码 `382915`）：

| 报名 ID | 学号 | 姓名 |
| --- | --- | --- |
| 6 | `2024020102` | 刘欣然 |
| 7 | `2024030101` | 孙浩宇 |
| 8 | `2024030102` | 周静怡 |

报名状态分布（24 条）：`APPROVED` 6、`COMPLETED` 6、`CHECKED_IN` 5、`PENDING` 3、`ABSENT` 2、`CANCELLED` 1、`REJECTED` 1。

---

## 四、测试路线

### 4.1 学生端主流程（约 10 分钟）

| 步骤 | 接口 | 预期 |
| --- | --- | --- |
| 1 | `POST /api/v1/auth/login` `{"student_no":"2024010101","password":"Student@2026"}` | `code=0`，返回 `access_token`、`is_first_login=true`，响应头 `Set-Cookie: refresh_token=...` |
| 2 | `GET /api/v1/me`（Bearer） | 手机号脱敏为 `138****0101`；`total_activities=1`、`total_hours=2.0` |
| 3 | `GET /api/v1/home` | `todo.checkin_available` 统计、公告只返回 2 条（第 3 条已过期不下发） |
| 4 | `GET /api/v1/activities` | 只出现 7 个中的 **5 个**（草稿 #5、已取消 #7 均不出现在学生列表）；`remaining` 与 §3 一致 |
| 5 | `GET /api/v1/activities/1` | `signup_state=CLOSED`（已报名且已签到则显示 `CHECKED_IN`）、`share_url` 非空 |
| 6 | `POST /api/v1/activities/2/registrations` | 需审核 → `status=PENDING`，`need_audit=true` |
| 7 | `GET /api/v1/me/registrations` | 能看到刚才的 PENDING；`status=FINISHED` 时返回已签到/已完成/缺席 |
| 8 | 换 `2024020102` 登录，`POST /api/v1/activities/1/checkin` `{"code":"382915"}` | `status=CHECKED_IN`，`hours=2.0`，`is_late` 依设置 |
| 9 | `GET /api/v1/me/stats` | `summary` / `type_distribution` / `monthly_trend`(6 个月) / `details` |
| 10 | `POST /api/v1/auth/refresh` | 凭 Cookie 换新 token |

> 第 2 步对 `2024010101` 的预期值：`activity_count=1`（活动1 已签到）、`hours_total=2.0`、`absent_count=1`（活动4 缺席）、`checkin_rate=50.0%`。

### 4.2 管理端主流程（约 15 分钟）

| 步骤 | 接口 | 预期 |
| --- | --- | --- |
| 1 | `GET /api/v1/admin/auth/captcha` → `POST /api/v1/admin/auth/login` | 带 `captcha_id`+`captcha_code`，返回管理员 token 与 `permissions` 列表 |
| 2 | `GET /api/v1/admin/me` | `role=SUPER_ADMIN`、`data_scope=ALL` |
| 3 | `GET /api/v1/admin/dashboard` | 见 §6 参考值 |
| 4 | `GET /api/v1/admin/activities` | 7 条（含草稿），`registered_count`/`checkin_count` 为聚合值 |
| 5 | `GET /api/v1/admin/activities/1` | 含 `checkin_code`、`share_url`、`stats` |
| 6 | `GET /api/v1/admin/activities/2/registrations?status=PENDING` | 3 条待审核（报名 9/10/11） |
| 7 | `POST /api/v1/admin/registrations/9/approve` | 通过；再对 10、11 走 `batch-audit` |
| 8 | `POST /api/v1/admin/registrations/12/reject` `{"reason":"..."}` | 拒绝；不填理由应 40001 |
| 9 | `GET /api/v1/admin/activities/1/checkin-board` | `expected=8`、`checked_in=5`、`checkin_rate=62.5%`、`unchecked` 3 人、学号脱敏 |
| 10 | `GET /api/v1/admin/activities/1/export/registrations` | 下载 `图书馆志愿整理服务_签到表_YYYYMMDD.xlsx` |
| 11 | `POST /api/v1/admin/registrations/7/checkin` `{"reason":"设备故障，人工补签"}` | 补签成功、`checkin_method=MANUAL` |
| 12 | `DELETE /api/v1/admin/registrations/7/checkin` `{"reason":"撤销测试"}` | 仅**超管**可撤销，状态回 `APPROVED` |
| 13 | `PATCH /api/v1/admin/students/1` `{"status":"DISABLED"}` | 停用后该学生无法登录 |
| 14 | `PATCH /api/v1/admin/settings` `{"items":[{"key":"record_checkin_ip","value":"true"}]}` | 仅超管；返回前后值 |
| 15 | `GET /api/v1/admin/logs`、`/logs/export` | 普通管理员只能看到自己的日志 |

### 4.3 数据范围（重点，易错）

用 `csadmin` 登录后：

- `GET /admin/activities` 只返回 **计算机学院管理员创建**的活动（#2、#7）和 #1（超管创建但… 见下）；
- `GET /admin/students` 只返回 **计算机学院** 学生（6 人：2024010101~0104、2023010101、2023010102）；
- 访问其他学院学生的 `GET /admin/students/{id}/records` → **40300**；
- 用 `teacher01` 登录：只能看到自己创建的活动（#3、#5）。

> 说明：`activities` 表无学院字段，`COLLEGE` 范围按"创建人属于本学院"推断（`csadmin` 是计算机学院管理员），这是对齐数据库设计后的既定规则，详见 README「数据库对接」章节。

---

## 五、请求示例

### 5.1 管理端验证码（手工取码）

验证码存在**进程内存**里，数据库查不到，需从接口返回的 SVG 里肉眼读取：

```powershell
$c = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/admin/auth/captcha"
$c.data.captcha_id          # 记住这个 id
[IO.File]::WriteAllBytes("$PWD\captcha.svg", [Convert]::FromBase64String($c.data.image.Split(',')[1]))
Start-Process "$PWD\captcha.svg"   # 打开图片，读出 4 个字符
```

然后在 `POST /api/v1/admin/auth/login` 里填 `captcha_id` 与该 4 位字符（**不区分大小写，因为服务端统一转大写；但只能使用一次**）。

### 5.2 学生登录并调用接口（PowerShell）

```powershell
$base = "http://127.0.0.1:8000/api/v1"
$login = Invoke-RestMethod "$base/auth/login" -Method Post -ContentType "application/json" `
         -Body '{"student_no":"2024020102","password":"Student@2026"}'
$h = @{ Authorization = "Bearer $($login.data.access_token)" }

Invoke-RestMethod "$base/activities/1/checkin" -Method Post -Headers $h `
    -ContentType "application/json" -Body '{"code":"382915"}'
```

### 5.3 curl 版本

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"student_no":"2024020102","password":"Student@2026"}' | jq -r .data.access_token)

curl -X POST http://127.0.0.1:8000/api/v1/activities/1/checkin \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"code":"382915"}'
```

---

## 六、错误码触发用例表

| 目标错误码 | 触发方法 | 期望 |
| --- | --- | --- |
| `40001` | `PATCH /me` 提交 `{"name":"张三"}` | 400，`data.field=name` |
| `40100` | 不带 Authorization 调 `/me` | 401 |
| `40101` | 密码输错 | 401；连错 5 次转 40103 |
| `40102` | 用 `2023010102` 登录（已停用） | 403 |
| `40103` | 同一账号故意输错密码 5 次 | 429，提示剩余分钟 |
| `40300` | 用 `csadmin` 查外国语学院学生记录；或用学生 token 调 `/admin/me` | 403 |
| `40400` | `GET /activities/999`；`GET /share/aaaaaaaaaaaa` | 404 |
| `40901` | 用 `2024010103` 报名活动 3（2/2 满员） | 409 |
| `40902` | 同一学生对同一活动重复报名（如 `2024010101` 报名活动 1） | 409 |
| `40903` | 报名活动 6（报名未开始） | 409 |
| `40904` | 报名活动 7（已取消）；或 `PATCH /admin/activities/1` 传错 `version` | 409 |
| `40905` | 签到码输成 `000000` | 409 |
| `40906` | 对活动 4（已结束）签到 | 409 |
| `40907` | 用 `2024010103`（活动 2 待审核）签到活动 2 | 409 |
| `40908` | 对活动 1 已签到的报名再签一次 | 409 |
| `40909` | 连续输错签到码 5 次后再签 | 429，锁定 10 分钟 |
| `40910` | 先 `POST /admin/activities/1/reset-code`，再用旧码 `382915` 签到 | 409（旧码已失效） |
| `42900` | 1 分钟内连续请求登录接口 11 次 | 429 |

**额外行为验证**

- **幂等**：同一次报名在 5 秒内重复提交 → 第二次返回 `message="重复提交，返回首次结果"` 且 `registration_id` 相同；
- **名额释放**：取消活动 2 的报名后，「已占名额」立即减少（聚合而来，无冗余计数）；
- **强制改密**：`2024010101` 登录后 `is_first_login=true`，调 `POST /auth/password` 后该字段变 `false`，且**其他会话立即失效**；
- **会话上限**：同一账号登录 4 次，最早的会话失效（旧 token 调 `/me` 返回 40100）；
- **软删除**：`DELETE /admin/activities/5`（草稿）后，列表不再出现，但记录仍在库中（`deleted_at` 非空）。

---

## 七、参考数值（便于核对）

以 `superadmin`、`range=all` 调 `GET /admin/stats/overview`：

```
activity_count   = 5      # 排除草稿 #5 与已取消 #7
signup_count     = 23     # 上述 5 个活动的报名（不含 #7 的 1 条）
checkin_count    = 11     # 5 已签到 + 6 已完成
absent_count     = 2
checkin_rate     = 84.6%
participant_count= 10     # 去重（活动1 的 5 人 ∪ 活动4 的 6 人）
hours_total      = 19.0   # 5×2.0 + 6×1.5
```

> 默认 `range=30d` 时只统计**已开始**的活动（#1、#4），故 `activity_count=2`、`signup_count=16`；想看全量请传 `range=all`。
>
> 统计口径与活动列表一致：**排除草稿与已取消活动**（已取消活动的报名已置 `CANCELLED`，不计入签到率）。

`GET /admin/dashboard`（超管，当前时点）：

```
ongoing_activity_count = 1    # 活动 1
pending_audit_count    = 3    # 活动 2 的待审核
today_checkin_count    = 5    # 今天 5 人签到
term_participant_count = 10
```

`GET /admin/activities/1/checkin-board`：

```
expected=8  checked_in=5  checkin_rate=62.5%  late_count=0  unchecked=3
```

---

## 八、数据重置

演示数据被改乱后，一条命令恢复（**会清空演示表，然后按当前时间重新生成**）：

```powershell
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p `
  --default-character-set=utf8mb4 lab_checkin `
  --execute="source $((Resolve-Path 'sql\04_demo_data.sql').Path)"
```

要点：

- 脚本内所有时间都基于 `NOW()` 相对计算，因此**任何时候重跑，活动 1 都处于"进行中 + 签到窗口开放"**；
- 主键固定（users 1–12、activities 1–7、registrations 1–24），文档里的 ID 长期有效；
- `dict_items` 与 `settings` 不会被清空（由 `03_init_data.sql` 维护，可重复执行）；
- 重置后 `auth_sessions` 会被清空，**所有已登录 token 立即失效**，需重新登录。

只重建结构（不动其他库）：

```powershell
mysql -u root -p -e "DROP DATABASE IF EXISTS lab_checkin; CREATE DATABASE lab_checkin DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
mysql -u root -p lab_checkin --execute="source sql/01_schema_core.sql"
mysql -u root -p lab_checkin --execute="source sql/02_schema_supplement.sql"
mysql -u root -p lab_checkin --execute="source sql/03_init_data.sql"
mysql -u root -p lab_checkin --execute="source sql/04_demo_data.sql"
```

---

## 九、已知限制与注意事项

1. **验证码 / 限流 / 幂等 / 签到连错计数都在进程内存**。单进程正常；若用 `--workers N` 或多实例，会出现"取图在 A 进程、校验落到 B 进程"的随机失败。测试请保持 **单 worker**。
2. **重启服务会清空**上述内存状态：验证码全部失效、锁定计时归零、幂等窗口清空。
3. **`tests/` 自动化脚本已移除**：原 `smoke_test.py`、`test_state_machine.py` 依赖已删除的 `app/seed.py` 与旧表结构，已清理。全部验证请按本文档的路线手工执行，或用 `/docs`；如需恢复自动化回归，可按本文用例重新生成。
4. `DEBUG=true` 时 `GET /me` 会额外返回 `mobile_raw`（未脱敏手机号），生产需关闭。
5. `2024010101` 首次登录必须改密后才能正常使用其他功能（前端据此跳转改密页）。
6. 导出文件落在 `storage/exports/`，路由 `/api/v1/exports/{task_id}` 下载，7 天过期。
7. 时间统一东八区：数据库存本地时间，接口输出 `+08:00`；`sql/04_demo_data.sql` 已 `SET time_zone='+08:00'`，与应用一致。

---

## 十、验收清单

- [ ] `/api/v1/health` 返回 `database: ok`
- [ ] 学生登录 / 刷新 / 退出 全通
- [ ] 活动列表不含草稿与已取消活动，`remaining` 与库内聚合一致
- [ ] 报名 → 审核 → 签到 → 撤销 全链路走通
- [ ] 名额已满 / 重复报名 / 报名未开始 / 活动取消 四种 409 都能触发
- [ ] 签到 8 步校验中至少验证 40905、40906、40907、40908、40909、40910
- [ ] 学生 token 访问管理端返回 403
- [ ] `csadmin` 只能看到计算机学院数据
- [ ] 导出 xlsx 可打开，字段为「学号/姓名/学院/班级/报名时间/签到时间/签到方式/签到状态/学时」
- [ ] 统计数值与 §7 一致
