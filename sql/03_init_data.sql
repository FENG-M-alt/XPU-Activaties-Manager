-- =============================================================================
-- 劳动活动签到系统 · 初始化数据（《数据库设计说明书 V1.0》9.1 / 9.2）
-- 可重复执行：使用 ON DUPLICATE KEY UPDATE 保证幂等
-- =============================================================================

SET NAMES utf8mb4;

-- -----------------------------------------------------------------------------
-- 9.1 字典初始值
-- -----------------------------------------------------------------------------
INSERT INTO `dict_items` (`dict_type`, `label`, `value`, `sort`, `enabled`) VALUES
  ('activity_type', '日常生活性劳动', 'daily',        1, 1),
  ('activity_type', '生产性劳动',     'productive',   2, 1),
  ('activity_type', '服务性劳动',     'service',      3, 1),
  ('activity_type', '专业性劳动',     'professional', 4, 1),
  ('activity_type', '创造性劳动',     'creative',     5, 1),
  ('reject_reason', '名额已满',       'full',         1, 1),
  ('reject_reason', '不符合活动要求', 'unqualified',  2, 1),
  ('reject_reason', '信息不完整',     'incomplete',   3, 1),
  ('reject_reason', '其他',           'other',        4, 1),
  ('notice_type',   '系统公告',       'system',       1, 1),
  ('notice_type',   '活动通知',       'activity',     2, 1)
ON DUPLICATE KEY UPDATE
  `label` = VALUES(`label`),
  `sort`  = VALUES(`sort`),
  `enabled` = VALUES(`enabled`);

-- -----------------------------------------------------------------------------
-- 9.2 系统参数默认值
-- -----------------------------------------------------------------------------
INSERT INTO `settings` (`key`, `value`, `remark`) VALUES
  ('checkin_before_default', '30',             '新建活动默认提前签到分钟数'),
  ('checkin_after_default',  '30',             '新建活动默认延后签到分钟数'),
  ('init_password_rule',     'id_card_tail6',  '初始密码规则，无身份证号时取学号后 6 位'),
  ('student_login_lock',     '5:10',           '学生端 5 次失败锁定 10 分钟'),
  ('admin_login_lock',       '5:15',           '管理端 5 次失败锁定 15 分钟'),
  ('share_expire_days',      '0',              '0 表示长期有效，N 表示活动结束后 N 天失效'),
  ('allow_student_cancel',   'true',           '是否允许学生取消报名'),
  ('checkin_makeup_days',    '7',              '补签期限（活动结束后天数）'),
  ('export_file_retain_days','7',              '异步导出文件保留天数'),
  ('record_checkin_ip',      'false',          '是否记录签到 IP'),
  ('checkin_late_grace_minutes', '0',          '迟到判定宽限分钟数（应用级补充参数）')
ON DUPLICATE KEY UPDATE
  `remark` = VALUES(`remark`);

-- -----------------------------------------------------------------------------
-- 管理员账号（示例，生产请删除或改密）
-- 密码哈希为 bcrypt(cost=12)，对应明文 Admin@123456
-- 首次启动应用时也可由 AUTO_SEED 自动写入；此处仅提供纯 SQL 初始化路径
-- -----------------------------------------------------------------------------
-- 注意：bcrypt 哈希无法在 SQL 中生成，如需纯 SQL 建库请先不插入账号，
--       启动应用并保持 AUTO_SEED=true，由应用完成管理员与示例学生写入。
