-- =============================================================================
-- 劳动活动签到系统 · 补充表结构（MySQL 8.0 / InnoDB / utf8mb4）
-- -----------------------------------------------------------------------------
-- 这 3 张表不在《数据库设计说明书 V1.0》的 9 张表之内，但接口契约必须依赖：
--   auth_sessions  接口文档 2.2：同账号最多 3 个会话、改密后其他会话失效、退出登录
--   notices        接口文档 4.11 / U-17：公告列表与有效期
--   export_tasks   接口文档 A-21 / A-32：异步导出任务与 7 天有效期下载
-- 若评审要求严格只保留 9 张表，可移除本文件，同时需接受上述三个能力降级
-- （会话无法撤销、公告改为静态配置、异步导出不可用）。
-- =============================================================================

SET NAMES utf8mb4;

-- -----------------------------------------------------------------------------
-- auth_sessions 登录会话（refresh token 摘要，可撤销）
-- -----------------------------------------------------------------------------
CREATE TABLE `auth_sessions` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(40) NOT NULL,
  `account_type` VARCHAR(16) NOT NULL COMMENT 'user/admin',
  `account_id` BIGINT UNSIGNED NOT NULL,
  `refresh_hash` VARCHAR(64) NOT NULL COMMENT 'refresh token 的 SHA-256 摘要',
  `issued_at` DATETIME NOT NULL,
  `expires_at` DATETIME NOT NULL,
  `revoked_at` DATETIME DEFAULT NULL,
  `last_used_at` DATETIME DEFAULT NULL,
  `ip` VARCHAR(45) DEFAULT NULL,
  `user_agent` VARCHAR(255) DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_session_id` (`session_id`),
  KEY `idx_account` (`account_type`, `account_id`, `expires_at`),
  KEY `idx_refresh_hash` (`refresh_hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='登录会话';

-- -----------------------------------------------------------------------------
-- notices 公告
-- -----------------------------------------------------------------------------
CREATE TABLE `notices` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `title` VARCHAR(200) NOT NULL,
  `content` VARCHAR(4000) NOT NULL,
  `notice_type` VARCHAR(32) NOT NULL DEFAULT 'system' COMMENT 'dict_items.notice_type',
  `is_pinned` TINYINT(1) NOT NULL DEFAULT 0,
  `start_at` DATETIME DEFAULT NULL,
  `end_at` DATETIME DEFAULT NULL,
  `is_active` TINYINT(1) NOT NULL DEFAULT 1,
  `created_by` BIGINT UNSIGNED DEFAULT NULL,
  `deleted_at` DATETIME DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_active_time` (`is_active`, `start_at`, `end_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='公告';

-- -----------------------------------------------------------------------------
-- export_tasks 异步导出任务
-- -----------------------------------------------------------------------------
CREATE TABLE `export_tasks` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `task_id` VARCHAR(40) NOT NULL,
  `activity_id` BIGINT UNSIGNED DEFAULT NULL,
  `export_type` VARCHAR(30) NOT NULL DEFAULT 'registrations',
  `file_name` VARCHAR(255) DEFAULT NULL,
  `file_path` VARCHAR(500) DEFAULT NULL,
  `row_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `status` VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  `error` VARCHAR(500) DEFAULT NULL,
  `created_by` BIGINT UNSIGNED DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expires_at` DATETIME NOT NULL,
  `downloaded_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_task_id` (`task_id`),
  KEY `idx_created_by` (`created_by`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='导出任务';
