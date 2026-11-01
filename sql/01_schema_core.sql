-- =============================================================================
-- 劳动活动签到系统 · 核心表结构（MySQL 8.0 / InnoDB / utf8mb4）
-- 依据《数据库设计说明书 V1.0》附录 A，逐字对应 9 张业务表
-- 文档编号：LAB-CHECKIN-DB-001
-- -----------------------------------------------------------------------------
-- 使用方式
--   mysql -u <user> -p -e "CREATE DATABASE lab_checkin DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
--   mysql -u <user> -p lab_checkin < sql/01_schema_core.sql
--   mysql -u <user> -p lab_checkin < sql/02_schema_supplement.sql
--   mysql -u <user> -p lab_checkin < sql/03_init_data.sql
-- -----------------------------------------------------------------------------
-- 设计约定
--   · 主键 BIGINT UNSIGNED AUTO_INCREMENT；不使用数据库外键约束
--   · 时间统一 DATETIME，存东八区本地时间（应用层会话时区 +08:00）
--   · 枚举用 VARCHAR 存英文代码，不使用 MySQL ENUM
--   · 软删除使用 deleted_at；registrations / checkin_logs / operation_logs 不做软删除
-- =============================================================================

SET NAMES utf8mb4;

-- -----------------------------------------------------------------------------
-- 4.1 users 学生账号表
-- -----------------------------------------------------------------------------
CREATE TABLE `users` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `student_no` VARCHAR(20) NOT NULL COMMENT '学号',
  `name` VARCHAR(50) NOT NULL COMMENT '姓名',
  `college` VARCHAR(50) NOT NULL COMMENT '学院',
  `major` VARCHAR(50) DEFAULT NULL COMMENT '专业',
  `class_name` VARCHAR(50) DEFAULT NULL COMMENT '班级',
  `grade` SMALLINT UNSIGNED DEFAULT NULL COMMENT '入学年份',
  `mobile` VARCHAR(20) DEFAULT NULL COMMENT '手机号',
  `id_card_enc` VARCHAR(190) DEFAULT NULL COMMENT '身份证号密文',
  `password_hash` VARCHAR(100) NOT NULL COMMENT '密码哈希',
  `status` VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/DISABLED',
  `is_first_login` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '需强制改密',
  `import_batch_id` BIGINT UNSIGNED DEFAULT NULL COMMENT '导入批次',
  `login_fail_count` TINYINT UNSIGNED NOT NULL DEFAULT 0,
  `locked_until` DATETIME DEFAULT NULL,
  `last_login_at` DATETIME DEFAULT NULL,
  `last_login_ip` VARCHAR(45) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_student_no` (`student_no`),
  KEY `idx_college` (`college`),
  KEY `idx_status` (`status`),
  KEY `idx_import_batch` (`import_batch_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生账号';

-- -----------------------------------------------------------------------------
-- 4.2 admins 管理员账号表
-- -----------------------------------------------------------------------------
CREATE TABLE `admins` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(50) NOT NULL,
  `name` VARCHAR(50) NOT NULL,
  `password_hash` VARCHAR(100) NOT NULL,
  `role` VARCHAR(20) NOT NULL DEFAULT 'ADMIN',
  `data_scope` VARCHAR(16) NOT NULL DEFAULT 'SELF',
  `college` VARCHAR(50) DEFAULT NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
  `is_first_login` TINYINT(1) NOT NULL DEFAULT 1,
  `login_fail_count` TINYINT UNSIGNED NOT NULL DEFAULT 0,
  `locked_until` DATETIME DEFAULT NULL,
  `last_login_at` DATETIME DEFAULT NULL,
  `last_login_ip` VARCHAR(45) DEFAULT NULL,
  `created_by` BIGINT UNSIGNED DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`),
  KEY `idx_role_status` (`role`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理员账号';

-- -----------------------------------------------------------------------------
-- 4.3 activities 活动表
-- -----------------------------------------------------------------------------
CREATE TABLE `activities` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `title` VARCHAR(60) NOT NULL,
  `type_id` BIGINT UNSIGNED NOT NULL COMMENT '字典项 ID',
  `host` VARCHAR(50) NOT NULL,
  `organizer` VARCHAR(50) DEFAULT NULL,
  `location` VARCHAR(100) NOT NULL,
  `cover_url` VARCHAR(255) DEFAULT NULL,
  `description` MEDIUMTEXT NOT NULL,
  `notice` TEXT,
  `contact_name` VARCHAR(20) NOT NULL,
  `contact_phone` VARCHAR(20) NOT NULL,
  `signup_start` DATETIME NOT NULL,
  `signup_end` DATETIME NOT NULL,
  `start_time` DATETIME NOT NULL,
  `end_time` DATETIME NOT NULL,
  `capacity` INT UNSIGNED NOT NULL,
  `need_audit` TINYINT(1) NOT NULL DEFAULT 0,
  `allow_cancel` TINYINT(1) NOT NULL DEFAULT 1,
  `hours` DECIMAL(4,1) NOT NULL DEFAULT 1.0,
  `checkin_before` SMALLINT UNSIGNED NOT NULL DEFAULT 30,
  `checkin_after` SMALLINT UNSIGNED NOT NULL DEFAULT 30,
  `checkin_code` CHAR(6) DEFAULT NULL,
  `checkin_code_generated_at` DATETIME DEFAULT NULL,
  `checkin_code_reset_at` DATETIME DEFAULT NULL,
  `share_token` CHAR(12) DEFAULT NULL,
  `share_expire_at` DATETIME DEFAULT NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'DRAFT',
  `version` INT UNSIGNED NOT NULL DEFAULT 1,
  `created_by` BIGINT UNSIGNED NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_checkin_code` (`checkin_code`),
  UNIQUE KEY `uk_share_token` (`share_token`),
  KEY `idx_status_start` (`status`, `start_time`),
  KEY `idx_type` (`type_id`),
  KEY `idx_created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='活动';

-- -----------------------------------------------------------------------------
-- 4.4 registrations 报名表（不做软删除；取消复用原行）
-- -----------------------------------------------------------------------------
CREATE TABLE `registrations` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `activity_id` BIGINT UNSIGNED NOT NULL,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  `college_snapshot` VARCHAR(50) NOT NULL,
  `major_snapshot` VARCHAR(50) DEFAULT NULL,
  `class_snapshot` VARCHAR(50) DEFAULT NULL,
  `signup_at` DATETIME NOT NULL,
  `audit_by` BIGINT UNSIGNED DEFAULT NULL,
  `audit_at` DATETIME DEFAULT NULL,
  `audit_remark` VARCHAR(200) DEFAULT NULL,
  `checkin_at` DATETIME DEFAULT NULL,
  `checkin_method` VARCHAR(16) DEFAULT NULL,
  `is_late` TINYINT(1) NOT NULL DEFAULT 0,
  `cancel_reason` VARCHAR(200) DEFAULT NULL,
  `source` VARCHAR(16) NOT NULL DEFAULT 'SELF',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_activity_user` (`activity_id`, `user_id`),
  KEY `idx_user_status` (`user_id`, `status`),
  KEY `idx_activity_status` (`activity_id`, `status`),
  KEY `idx_checkin_at` (`checkin_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='报名记录';

-- -----------------------------------------------------------------------------
-- 4.5 checkin_logs 签到流水表（只追加）
-- -----------------------------------------------------------------------------
CREATE TABLE `checkin_logs` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `registration_id` BIGINT UNSIGNED NOT NULL,
  `action` VARCHAR(16) NOT NULL COMMENT 'CHECKIN/UNDO',
  `method` VARCHAR(16) NOT NULL COMMENT 'CODE/MANUAL',
  `operator_id` BIGINT UNSIGNED DEFAULT NULL,
  `reason` VARCHAR(200) DEFAULT NULL,
  `ip` VARCHAR(45) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_registration` (`registration_id`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='签到流水';

-- -----------------------------------------------------------------------------
-- 4.6 import_batches 导入批次表
-- -----------------------------------------------------------------------------
CREATE TABLE `import_batches` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `file_name` VARCHAR(255) NOT NULL,
  `operator_id` BIGINT UNSIGNED NOT NULL,
  `total` INT UNSIGNED NOT NULL DEFAULT 0,
  `success` INT UNSIGNED NOT NULL DEFAULT 0,
  `failed` INT UNSIGNED NOT NULL DEFAULT 0,
  `status` VARCHAR(16) NOT NULL DEFAULT 'SUCCESS',
  `error_file_url` VARCHAR(255) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生导入批次';

-- -----------------------------------------------------------------------------
-- 4.7 operation_logs 操作日志表（只追加）
-- -----------------------------------------------------------------------------
CREATE TABLE `operation_logs` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `operator_type` VARCHAR(16) NOT NULL COMMENT 'ADMIN/STUDENT/SYSTEM',
  `operator_id` BIGINT UNSIGNED DEFAULT NULL,
  `action` VARCHAR(50) NOT NULL,
  `target_type` VARCHAR(32) DEFAULT NULL,
  `target_id` BIGINT UNSIGNED DEFAULT NULL,
  `detail` JSON DEFAULT NULL,
  `ip` VARCHAR(45) DEFAULT NULL,
  `ua` VARCHAR(255) DEFAULT NULL,
  `result` VARCHAR(16) NOT NULL DEFAULT 'SUCCESS',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_created_at` (`created_at`),
  KEY `idx_operator` (`operator_id`, `created_at`),
  KEY `idx_target` (`target_type`, `target_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='操作日志';

-- -----------------------------------------------------------------------------
-- 4.8 settings 系统参数表（key 为保留字，需反引号）
-- -----------------------------------------------------------------------------
CREATE TABLE `settings` (
  `key` VARCHAR(50) NOT NULL,
  `value` VARCHAR(500) NOT NULL,
  `remark` VARCHAR(200) DEFAULT NULL,
  `updated_by` BIGINT UNSIGNED DEFAULT NULL,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统参数';

-- -----------------------------------------------------------------------------
-- 4.9 dict_items 字典项表
-- -----------------------------------------------------------------------------
CREATE TABLE `dict_items` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `dict_type` VARCHAR(32) NOT NULL,
  `label` VARCHAR(50) NOT NULL,
  `value` VARCHAR(32) NOT NULL,
  `sort` SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  `enabled` TINYINT(1) NOT NULL DEFAULT 1,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_type_value` (`dict_type`, `value`),
  KEY `idx_type_sort` (`dict_type`, `sort`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='字典项';
