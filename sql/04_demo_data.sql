-- =============================================================================
-- 劳动活动签到系统 · 演示/测试数据
-- -----------------------------------------------------------------------------
-- 用途：为接口联调准备一组覆盖各状态、各边界的数据。
-- 特性：
--   · 时间全部基于 NOW() 相对计算，任何时间点重跑都"当下有效"
--     （例如活动 1 始终处于"进行中且签到窗口开放"，可直接测签到）
--   · 显式指定主键，便于测试脚本引用固定 ID
--   · 可重复执行：先清空演示数据，再重新写入（dict_items / settings 不受影响）
--
-- ⚠️ 警告：本脚本会 DELETE 下列表的全部数据，请勿在有真实数据的库上执行：
--          checkin_logs / registrations / activities / notices /
--          operation_logs / auth_sessions / export_tasks / import_batches /
--          users / admins
--
-- 预置账号（bcrypt cost=12）
--   超级管理员  superadmin / Admin@123456
--   学院管理员  csadmin    / Admin@123456   data_scope=COLLEGE（计算机学院）
--   普通管理员  teacher01  / Admin@123456   data_scope=SELF
--   学生       2024010101 ~ 2024040102 / Student@2026
--              （2024010101 为首次登录，需强制改密）
-- =============================================================================

SET NAMES utf8mb4;
SET time_zone = '+08:00';

-- -----------------------------------------------------------------------------
-- 0. 清空演示数据
-- -----------------------------------------------------------------------------
DELETE FROM `checkin_logs`;
DELETE FROM `registrations`;
DELETE FROM `activities`;
DELETE FROM `notices`;
DELETE FROM `operation_logs`;
DELETE FROM `auth_sessions`;
DELETE FROM `export_tasks`;
DELETE FROM `import_batches`;
DELETE FROM `users`;
DELETE FROM `admins`;

-- 字典 ID 变量（避免硬编码，随 03_init_data.sql 的实际自增值走）
SET @t_daily        := (SELECT id FROM dict_items WHERE dict_type='activity_type' AND value='daily');
SET @t_productive   := (SELECT id FROM dict_items WHERE dict_type='activity_type' AND value='productive');
SET @t_service      := (SELECT id FROM dict_items WHERE dict_type='activity_type' AND value='service');
SET @t_professional := (SELECT id FROM dict_items WHERE dict_type='activity_type' AND value='professional');

-- 密码哈希（bcrypt，明文见文件头）
SET @h_admin := '$2b$12$LqGFIAMQ.6pEf1Tvs6K9D.UDINZ2FlVFNrb9.v8bkJEMquTuutT.G';
SET @h_stu   := '$2b$12$irLcJ7HjVcCdJIFPZecL8.Gq9DHh2N5YTH6baSwlp3SHhmKZIpSIC';

-- -----------------------------------------------------------------------------
-- 1. 管理员（3 个，覆盖三种数据范围）
-- -----------------------------------------------------------------------------
INSERT INTO `admins`
  (`id`, `username`, `name`, `password_hash`, `role`, `data_scope`, `college`,
   `status`, `is_first_login`, `login_fail_count`, `created_at`, `updated_at`) VALUES
  (1, 'superadmin', '系统管理员',       @h_admin, 'SUPER_ADMIN', 'ALL',     NULL,           'ACTIVE', 0, 0, NOW() - INTERVAL 60 DAY, NOW() - INTERVAL 60 DAY),
  (2, 'csadmin',    '计算机学院管理员', @h_admin, 'ADMIN',       'COLLEGE', '计算机学院',   'ACTIVE', 0, 0, NOW() - INTERVAL 60 DAY, NOW() - INTERVAL 60 DAY),
  (3, 'teacher01',  '辅导员张老师',     @h_admin, 'ADMIN',       'SELF',    NULL,           'ACTIVE', 0, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY);

-- -----------------------------------------------------------------------------
-- 2. 学生（12 个，4 个学院）
--    id=1 的 2024010101 为 is_first_login=1，用于测试强制改密
-- -----------------------------------------------------------------------------
INSERT INTO `users`
  (`id`, `student_no`, `name`, `college`, `major`, `class_name`, `grade`, `mobile`,
   `password_hash`, `status`, `is_first_login`, `login_fail_count`, `created_at`, `updated_at`) VALUES
  (1,  '2024010101', '张一鸣', '计算机学院',   '软件工程',               '软工2401', 2024, '13800000101', @h_stu, 'ACTIVE', 1, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY),
  (2,  '2024010102', '李思远', '计算机学院',   '计算机科学与技术',       '计科2401', 2024, '13800000102', @h_stu, 'ACTIVE', 0, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY),
  (3,  '2024010103', '王雨桐', '计算机学院',   '网络工程',               '网工2401', 2024, '13800000103', @h_stu, 'ACTIVE', 0, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY),
  (4,  '2024010104', '赵梓涵', '计算机学院',   '软件工程',               '软工2402', 2024, '13800000104', @h_stu, 'ACTIVE', 0, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY),
  (5,  '2024020101', '陈嘉俊', '外国语学院',   '英语',                   '英语2401', 2024, '13800000201', @h_stu, 'ACTIVE', 0, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY),
  (6,  '2024020102', '刘欣然', '外国语学院',   '日语',                   '日语2401', 2024, '13800000202', @h_stu, 'ACTIVE', 0, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY),
  (7,  '2024030101', '孙浩宇', '机械工程学院', '机械设计制造及其自动化', '机械2401', 2024, '13800000301', @h_stu, 'ACTIVE', 0, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY),
  (8,  '2024030102', '周静怡', '机械工程学院', '车辆工程',               '车辆2401', 2024, '13800000302', @h_stu, 'ACTIVE', 0, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY),
  (9,  '2024040101', '吴子墨', '经济管理学院', '工商管理',               '工商2401', 2024, '13800000401', @h_stu, 'ACTIVE', 0, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY),
  (10, '2024040102', '郑晓彤', '经济管理学院', '会计学',                 '会计2401', 2024, '13800000402', @h_stu, 'ACTIVE', 0, 0, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY),
  (11, '2023010101', '冯天佑', '计算机学院',   '人工智能',               '智能2301', 2023, '13800000501', @h_stu, 'ACTIVE', 0, 0, NOW() - INTERVAL 400 DAY, NOW() - INTERVAL 400 DAY),
  (12, '2023010102', '许安然', '计算机学院',   '数据科学',               '数科2301', 2023, '13800000502', @h_stu, 'DISABLED', 0, 0, NOW() - INTERVAL 400 DAY, NOW() - INTERVAL 400 DAY);

-- -----------------------------------------------------------------------------
-- 3. 公告（1 置顶 + 1 普通；1 条已过期用于验证自动下线）
-- -----------------------------------------------------------------------------
INSERT INTO `notices`
  (`id`, `title`, `content`, `notice_type`, `is_pinned`, `start_at`, `end_at`, `is_active`, `created_by`, `created_at`, `updated_at`) VALUES
  (1, '关于 2026 年秋季学期劳动教育活动安排的通知',
      '<p>各学院：</p><p>本学期劳动教育活动将于第 5 周起陆续开展，请同学们关注活动列表并按时报名、签到。签到码由现场工作人员公布。</p>',
      'system', 1, NOW() - INTERVAL 3 DAY, NOW() + INTERVAL 60 DAY, 1, 1, NOW() - INTERVAL 3 DAY, NOW() - INTERVAL 3 DAY),
  (2, '签到规范说明',
      '<p>请在活动开始前 30 分钟至结束后 30 分钟完成签到，逾期视为缺席。</p>',
      'system', 0, NOW() - INTERVAL 10 DAY, NOW() + INTERVAL 120 DAY, 1, 2, NOW() - INTERVAL 10 DAY, NOW() - INTERVAL 10 DAY),
  (3, '【已过期】上学期劳动学时统计通知',
      '<p>上学期学时已完成统计，本条公告已过有效期，不应出现在公告列表中。</p>',
      'system', 0, NOW() - INTERVAL 90 DAY, NOW() - INTERVAL 30 DAY, 1, 1, NOW() - INTERVAL 90 DAY, NOW() - INTERVAL 90 DAY);

-- -----------------------------------------------------------------------------
-- 4. 活动（7 个，覆盖全部状态与关键边界）
-- -----------------------------------------------------------------------------
INSERT INTO `activities`
  (`id`, `title`, `type_id`, `host`, `organizer`, `location`, `cover_url`, `description`, `notice`,
   `contact_name`, `contact_phone`, `signup_start`, `signup_end`, `start_time`, `end_time`,
   `capacity`, `need_audit`, `allow_cancel`, `hours`, `checkin_before`, `checkin_after`,
   `checkin_code`, `checkin_code_generated_at`, `checkin_code_reset_at`, `share_token`, `share_expire_at`,
   `status`, `version`, `created_by`, `created_at`, `updated_at`) VALUES
  -- ① 进行中 + 签到窗口开放（可直接用 382915 签到）
  (1, '图书馆志愿整理服务', @t_service, '校团委', '青年志愿者协会', '图书馆三层阅览室', NULL,
   '<p>协助图书馆整理书架、引导读者，服务时长 2 小时。</p>',
   '<p>请穿平底鞋，提前 10 分钟到岗。</p>',
   '王老师', '0571-88880001',
   NOW() - INTERVAL 5 DAY, NOW() - INTERVAL 1 HOUR,
   NOW() - INTERVAL 30 MINUTE, NOW() + INTERVAL 2 HOUR,
   30, 0, 1, 2.0, 60, 60,
   '382915', NOW() - INTERVAL 5 DAY, NULL, 'Ab3kL9mN2pQr', NULL,
   'ONGOING', 2, 2, NOW() - INTERVAL 6 DAY, NOW() - INTERVAL 1 DAY),

  -- ② 报名中 + 需审核（含待审核/已通过/已拒绝）
  (2, '敬老院慰问演出', @t_daily, '校学生会', '文艺部', '阳光敬老院', NULL,
   '<p>为敬老院老人表演节目并陪伴交流，需提前审核报名。</p>',
   '<p>报名后请等待管理员审核，审核通过后统一乘车前往。</p>',
   '李同学', '13800009999',
   NOW() - INTERVAL 1 DAY, NOW() + INTERVAL 2 DAY,
   NOW() + INTERVAL 3 DAY, NOW() + INTERVAL 3 DAY + INTERVAL 3 HOUR,
   20, 1, 1, 3.0, 30, 30,
   '741028', NOW() - INTERVAL 1 DAY, NULL, 'Zx7vB4nM1qWs', NULL,
   'REGISTERING', 1, 2, NOW() - INTERVAL 2 DAY, NOW() - INTERVAL 1 DAY),

  -- ③ 名额已满（capacity=2，已占 2）→ 报名返回 40901
  (3, '校园环保宣传周（名额已满）', @t_productive, '后勤保障处', '绿色环保协会', '西校区主干道', NULL,
   '<p>宣传活动现场协助，名额仅 2 人。</p>', NULL,
   '赵老师', '0571-88880002',
   NOW() - INTERVAL 1 DAY, NOW() + INTERVAL 4 DAY,
   NOW() + INTERVAL 5 DAY, NOW() + INTERVAL 5 DAY + INTERVAL 2 HOUR,
   2, 0, 1, 1.0, 30, 30,
   '506173', NOW() - INTERVAL 1 DAY, NULL, 'Qm8tR2vY6kLp', NULL,
   'REGISTERING', 1, 3, NOW() - INTERVAL 3 DAY, NOW() - INTERVAL 1 DAY),

  -- ④ 已结束（6 人已完成 / 2 人缺席）
  (4, '校园环境清洁行动', @t_daily, '后勤保障处', '学生会生活部', '东校区中心广场', NULL,
   '<p>清理校园公共区域垃圾，美化校园环境。</p>', NULL,
   '赵老师', '0571-88880003',
   NOW() - INTERVAL 15 DAY, NOW() - INTERVAL 11 DAY,
   NOW() - INTERVAL 10 DAY, NOW() - INTERVAL 10 DAY + INTERVAL 2 HOUR,
   40, 0, 1, 1.5, 30, 30,
   '194827', NOW() - INTERVAL 15 DAY, NULL, 'Hd5sK1wC9nTz', NULL,
   'FINISHED', 2, 1, NOW() - INTERVAL 16 DAY, NOW() - INTERVAL 10 DAY),

  -- ⑤ 草稿（不可报名、出现在管理端列表）
  (5, '劳动教育专题讲座（草稿）', @t_professional, '教务处', '教务处实践科', '学术报告厅', NULL,
   '<p>劳动教育政策解读与实务分享。</p>', NULL,
   '孙老师', '0571-88880004',
   NOW() + INTERVAL 1 DAY, NOW() + INTERVAL 6 DAY,
   NOW() + INTERVAL 7 DAY, NOW() + INTERVAL 7 DAY + INTERVAL 2 HOUR,
   100, 0, 1, 1.0, 30, 30,
   NULL, NULL, NULL, NULL, NULL,
   'DRAFT', 1, 3, NOW() - INTERVAL 1 DAY, NOW() - INTERVAL 1 DAY),

  -- ⑥ 已发布但报名未开始 → 详情 signup_state=NOT_STARTED，报名返回 40903
  (6, '秋季运动会志愿服务（报名未开始）', @t_service, '体育部', '青年志愿者协会', '东校区田径场', NULL,
   '<p>运动会现场引导与补给服务。</p>', NULL,
   '周老师', '0571-88880005',
   NOW() + INTERVAL 1 DAY, NOW() + INTERVAL 9 DAY,
   NOW() + INTERVAL 10 DAY, NOW() + INTERVAL 10 DAY + INTERVAL 4 HOUR,
   50, 0, 1, 4.0, 30, 30,
   '628401', NOW(), NULL, 'Vb2nM7xL4qRz', NULL,
   'PUBLISHED', 1, 2, NOW() - INTERVAL 1 DAY, NOW() - INTERVAL 1 DAY),

  -- ⑦ 已取消 → 报名返回 40904，分享页返回"活动已取消"
  (7, '旧衣物捐赠活动（已取消）', @t_daily, '校团委', '爱心社', '大学生活动中心', NULL,
   '<p>因场地调整，活动已取消。</p>', NULL,
   '钱老师', '0571-88880006',
   NOW() - INTERVAL 2 DAY, NOW() + INTERVAL 1 DAY,
   NOW() + INTERVAL 2 DAY, NOW() + INTERVAL 2 DAY + INTERVAL 2 HOUR,
   30, 0, 1, 1.0, 30, 30,
   '773905', NOW() - INTERVAL 2 DAY, NULL, 'Kp6yT3nW8mVs', NULL,
   'CANCELLED', 2, 2, NOW() - INTERVAL 4 DAY, NOW() - INTERVAL 1 DAY);

-- -----------------------------------------------------------------------------
-- 5. 报名（24 条，覆盖 PENDING/APPROVED/CHECKED_IN/COMPLETED/ABSENT/REJECTED/CANCELLED）
--    college/major/class 为报名时快照
-- -----------------------------------------------------------------------------
INSERT INTO `registrations`
  (`id`, `activity_id`, `user_id`, `status`, `college_snapshot`, `major_snapshot`, `class_snapshot`,
   `signup_at`, `audit_by`, `audit_at`, `audit_remark`, `checkin_at`, `checkin_method`, `is_late`,
   `cancel_reason`, `source`, `created_at`, `updated_at`) VALUES
  -- 活动①：5 人已签到，3 人已通过待签到
  (1,  1, 1, 'CHECKED_IN', '计算机学院',   '软件工程',               '软工2401', NOW() - INTERVAL 2 DAY, NULL, NULL, NULL, NOW() - INTERVAL 25 MINUTE, 'CODE', 0, NULL, 'SELF', NOW() - INTERVAL 2 DAY, NOW() - INTERVAL 25 MINUTE),
  (2,  1, 2, 'CHECKED_IN', '计算机学院',   '计算机科学与技术',       '计科2401', NOW() - INTERVAL 2 DAY, NULL, NULL, NULL, NOW() - INTERVAL 25 MINUTE, 'CODE', 0, NULL, 'SELF', NOW() - INTERVAL 2 DAY, NOW() - INTERVAL 25 MINUTE),
  (3,  1, 3, 'CHECKED_IN', '计算机学院',   '网络工程',               '网工2401', NOW() - INTERVAL 2 DAY, NULL, NULL, NULL, NOW() - INTERVAL 25 MINUTE, 'CODE', 0, NULL, 'SELF', NOW() - INTERVAL 2 DAY, NOW() - INTERVAL 25 MINUTE),
  (4,  1, 4, 'CHECKED_IN', '计算机学院',   '软件工程',               '软工2402', NOW() - INTERVAL 2 DAY, NULL, NULL, NULL, NOW() - INTERVAL 25 MINUTE, 'CODE', 0, NULL, 'SELF', NOW() - INTERVAL 2 DAY, NOW() - INTERVAL 25 MINUTE),
  (5,  1, 5, 'CHECKED_IN', '外国语学院',   '英语',                   '英语2401', NOW() - INTERVAL 2 DAY, NULL, NULL, NULL, NOW() - INTERVAL 25 MINUTE, 'CODE', 0, NULL, 'SELF', NOW() - INTERVAL 2 DAY, NOW() - INTERVAL 25 MINUTE),
  (6,  1, 6, 'APPROVED',   '外国语学院',   '日语',                   '日语2401', NOW() - INTERVAL 2 DAY, NULL, NULL, NULL, NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 2 DAY, NOW() - INTERVAL 40 MINUTE),
  (7,  1, 7, 'APPROVED',   '机械工程学院', '机械设计制造及其自动化', '机械2401', NOW() - INTERVAL 2 DAY, NULL, NULL, NULL, NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 2 DAY, NOW() - INTERVAL 2 DAY),
  (8,  1, 8, 'APPROVED',   '机械工程学院', '车辆工程',               '车辆2401', NOW() - INTERVAL 2 DAY, NULL, NULL, NULL, NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 2 DAY, NOW() - INTERVAL 2 DAY),

  -- 活动②：3 待审核 / 1 已通过 / 1 已拒绝
  (9,  2, 3, 'PENDING',    '计算机学院',   '网络工程',               '网工2401', NOW() - INTERVAL 20 HOUR, NULL, NULL, NULL, NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 20 HOUR, NOW() - INTERVAL 20 HOUR),
  (10, 2, 4, 'PENDING',    '计算机学院',   '软件工程',               '软工2402', NOW() - INTERVAL 18 HOUR, NULL, NULL, NULL, NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 18 HOUR, NOW() - INTERVAL 18 HOUR),
  (11, 2, 5, 'PENDING',    '外国语学院',   '英语',                   '英语2401', NOW() - INTERVAL 16 HOUR, NULL, NULL, NULL, NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 16 HOUR, NOW() - INTERVAL 16 HOUR),
  (12, 2, 6, 'APPROVED',   '外国语学院',   '日语',                   '日语2401', NOW() - INTERVAL 22 HOUR, 2, NOW() - INTERVAL 2 HOUR, '符合报名条件', NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 22 HOUR, NOW() - INTERVAL 2 HOUR),
  (13, 2, 7, 'REJECTED',   '机械工程学院', '机械设计制造及其自动化', '机械2401', NOW() - INTERVAL 21 HOUR, 2, NOW() - INTERVAL 3 HOUR, '不符合本次活动要求，建议参加其他志愿活动', NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 21 HOUR, NOW() - INTERVAL 3 HOUR),

  -- 活动③：2/2 已占满
  (14, 3, 1, 'APPROVED',   '计算机学院',   '软件工程',               '软工2401', NOW() - INTERVAL 12 HOUR, NULL, NULL, NULL, NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 12 HOUR, NOW() - INTERVAL 12 HOUR),
  (15, 3, 2, 'APPROVED',   '计算机学院',   '计算机科学与技术',       '计科2401', NOW() - INTERVAL 11 HOUR, NULL, NULL, NULL, NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 11 HOUR, NOW() - INTERVAL 11 HOUR),

  -- 活动④：6 已完成 / 2 缺席
  (16, 4, 5, 'COMPLETED',  '外国语学院',   '英语',                   '英语2401', NOW() - INTERVAL 14 DAY, NULL, NULL, NULL, NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE, 'CODE', 0, NULL, 'SELF', NOW() - INTERVAL 14 DAY, NOW() - INTERVAL 10 DAY),
  (17, 4, 6, 'COMPLETED',  '外国语学院',   '日语',                   '日语2401', NOW() - INTERVAL 14 DAY, NULL, NULL, NULL, NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE, 'CODE', 0, NULL, 'SELF', NOW() - INTERVAL 14 DAY, NOW() - INTERVAL 10 DAY),
  (18, 4, 7, 'COMPLETED',  '机械工程学院', '机械设计制造及其自动化', '机械2401', NOW() - INTERVAL 14 DAY, NULL, NULL, NULL, NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE, 'CODE', 1, NULL, 'SELF', NOW() - INTERVAL 14 DAY, NOW() - INTERVAL 10 DAY),
  (19, 4, 8, 'COMPLETED',  '机械工程学院', '车辆工程',               '车辆2401', NOW() - INTERVAL 14 DAY, NULL, NULL, NULL, NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE, 'CODE', 0, NULL, 'SELF', NOW() - INTERVAL 14 DAY, NOW() - INTERVAL 10 DAY),
  (20, 4, 9, 'COMPLETED',  '经济管理学院', '工商管理',               '工商2401', NOW() - INTERVAL 14 DAY, NULL, NULL, NULL, NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE, 'CODE', 0, NULL, 'SELF', NOW() - INTERVAL 14 DAY, NOW() - INTERVAL 10 DAY),
  (21, 4, 10,'COMPLETED',  '经济管理学院', '会计学',                 '会计2401', NOW() - INTERVAL 14 DAY, NULL, NULL, NULL, NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE, 'CODE', 0, NULL, 'SELF', NOW() - INTERVAL 14 DAY, NOW() - INTERVAL 10 DAY),
  (22, 4, 1, 'ABSENT',     '计算机学院',   '软件工程',               '软工2401', NOW() - INTERVAL 14 DAY, NULL, NULL, NULL, NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 14 DAY, NOW() - INTERVAL 10 DAY),
  (23, 4, 2, 'ABSENT',     '计算机学院',   '计算机科学与技术',       '计科2401', NOW() - INTERVAL 14 DAY, NULL, NULL, NULL, NULL, NULL, 0, NULL, 'SELF', NOW() - INTERVAL 14 DAY, NOW() - INTERVAL 10 DAY),

  -- 活动⑦：活动被取消，报名同步置为 CANCELLED
  (24, 7, 3, 'CANCELLED',  '计算机学院',   '网络工程',               '网工2401', NOW() - INTERVAL 3 DAY, NULL, NULL, NULL, NULL, NULL, 0, '活动取消：场地调整', 'SELF', NOW() - INTERVAL 3 DAY, NOW() - INTERVAL 1 DAY);

-- -----------------------------------------------------------------------------
-- 6. 签到流水（只追加）
--    · 学生自助签到：operator_id = NULL，method = CODE
--    · 报名 6 上有一条"补签 + 撤销"的完整痕迹，可用于验证 U-14 的 checkin_logs
-- -----------------------------------------------------------------------------
INSERT INTO `checkin_logs` (`id`, `registration_id`, `action`, `method`, `operator_id`, `reason`, `ip`, `created_at`) VALUES
  (1,  1, 'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.1.11', NOW() - INTERVAL 25 MINUTE),
  (2,  2, 'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.1.12', NOW() - INTERVAL 25 MINUTE),
  (3,  3, 'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.1.13', NOW() - INTERVAL 25 MINUTE),
  (4,  4, 'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.1.14', NOW() - INTERVAL 25 MINUTE),
  (5,  5, 'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.1.15', NOW() - INTERVAL 25 MINUTE),
  (6,  6, 'CHECKIN', 'MANUAL', 2,    '现场签到设备故障，管理员人工补签',   '10.20.0.2',  NOW() - INTERVAL 40 MINUTE),
  (7,  6, 'UNDO',    'MANUAL', 1,    '重复补签，已撤销',                   '10.20.0.1',  NOW() - INTERVAL 35 MINUTE),
  (8,  16,'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.2.11', NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE),
  (9,  17,'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.2.12', NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE),
  (10, 18,'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.2.13', NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE),
  (11, 19,'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.2.14', NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE),
  (12, 20,'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.2.15', NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE),
  (13, 21,'CHECKIN', 'CODE',   NULL, NULL,                                   '10.20.2.16', NOW() - INTERVAL 10 DAY + INTERVAL 5 MINUTE);

-- -----------------------------------------------------------------------------
-- 7. 操作日志（供 A-35 查询/导出）
-- -----------------------------------------------------------------------------
INSERT INTO `operation_logs`
  (`id`, `operator_type`, `operator_id`, `action`, `target_type`, `target_id`, `detail`, `ip`, `ua`, `result`, `created_at`) VALUES
  (1, 'ADMIN',   1, 'ACTIVITY_PUBLISH',       'activities',    1,    JSON_OBJECT('checkin_code', '382915'),            '10.20.0.1', 'Mozilla/5.0 (Windows NT 10.0)', 'SUCCESS', NOW() - INTERVAL 6 DAY),
  (2, 'ADMIN',   2, 'ACTIVITY_PUBLISH',       'activities',    2,    JSON_OBJECT('checkin_code', '741028'),            '10.20.0.2', 'Mozilla/5.0 (Windows NT 10.0)', 'SUCCESS', NOW() - INTERVAL 2 DAY),
  (3, 'ADMIN',   2, 'REGISTRATION_APPROVE',   'registrations', 12,   JSON_OBJECT('activity_id', 2),                     '10.20.0.2', 'Mozilla/5.0 (Windows NT 10.0)', 'SUCCESS', NOW() - INTERVAL 2 HOUR),
  (4, 'ADMIN',   2, 'REGISTRATION_REJECT',    'registrations', 13,   JSON_OBJECT('reason', '不符合本次活动要求'),       '10.20.0.2', 'Mozilla/5.0 (Windows NT 10.0)', 'SUCCESS', NOW() - INTERVAL 3 HOUR),
  (5, 'ADMIN',   1, 'REGISTRATION_UNDO_CHECKIN','registrations', 6,   JSON_OBJECT('reason', '重复补签，已撤销'),         '10.20.0.1', 'Mozilla/5.0 (Windows NT 10.0)', 'SUCCESS', NOW() - INTERVAL 35 MINUTE),
  (6, 'STUDENT', 1, 'LOGIN',                  'users',         1,    NULL,                                              '10.20.1.11','Mozilla/5.0 (Windows NT 10.0)', 'SUCCESS', NOW() - INTERVAL 2 HOUR);
