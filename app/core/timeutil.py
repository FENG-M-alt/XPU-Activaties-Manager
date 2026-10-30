"""时间工具：数据库存储东八区本地时间（naive），对外输出 ISO 8601 带 +08:00 偏移。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.config import LOCAL_TZ


def now_local() -> datetime:
    """当前东八区时间（naive，用于入库与比较）。"""
    return datetime.now(LOCAL_TZ).replace(tzinfo=None, microsecond=0)


def today_local() -> date:
    return now_local().date()


def as_aware(dt: datetime | None) -> datetime | None:
    """naive -> 带 +08:00 的 aware。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def to_naive_local(dt: datetime | None) -> datetime | None:
    """任意 aware -> 东八区 naive；用于入库。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(LOCAL_TZ).replace(tzinfo=None)


def to_iso(dt: datetime | None) -> str | None:
    aware = as_aware(dt)
    return aware.isoformat() if aware else None


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def day_start(d: date | datetime) -> datetime:
    if isinstance(d, datetime):
        d = d.date()
    return datetime.combine(d, time.min)


def day_end(d: date | datetime) -> datetime:
    if isinstance(d, datetime):
        d = d.date()
    return datetime.combine(d, time.max)


def month_range(anchor: date, months_back: int) -> tuple[datetime, datetime]:
    """以 anchor 所在月为终点，向前 months_back 个月（含当月）的区间。"""
    y, m = anchor.year, anchor.month
    total = y * 12 + (m - 1) - (months_back - 1)
    sy, sm = divmod(total, 12)
    sm += 1
    start = datetime(sy, sm, 1)
    end = day_end(anchor) if total == y * 12 + (m - 1) else datetime(
        (sy + 1) if sm == 12 else sy, 1 if sm == 12 else sm + 1, 1
    ) - timedelta(microseconds=1)
    return start, end


def time_range_bounds(time_range: str, start_date: date | None, end_date: date | None, now: datetime | None = None) -> tuple[datetime | None, datetime | None]:
    """活动列表的 time_range 参数（week / month / term / custom）。"""
    now = now or now_local()
    if time_range == "custom":
        start = day_start(start_date) if start_date else None
        end = day_end(end_date) if end_date else None
        return start, end
    if time_range == "week":
        start = day_start(now.date() - timedelta(days=now.weekday()))
        return start, start + timedelta(days=7) - timedelta(microseconds=1)
    if time_range == "month":
        start = datetime(now.year, now.month, 1)
        nxt = datetime(now.year + (1 if now.month == 12 else 0), 1 if now.month == 12 else now.month + 1, 1)
        return start, nxt - timedelta(microseconds=1)
    if time_range == "term":
        return term_bounds(now)
    return None, None


def term_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """学期区间：秋季 9/1—次年 2 月末，春季 3/1—8/31。"""
    now = now or now_local()
    y, m = now.year, now.month
    if m >= 9:
        start = datetime(y, 9, 1)
        end = datetime(y + 1, 3, 1) - timedelta(microseconds=1)
    elif m == 2:
        start = datetime(y - 1, 9, 1)
        end = datetime(y, 3, 1) - timedelta(microseconds=1)
    elif m >= 3:
        start = datetime(y, 3, 1)
        end = datetime(y, 9, 1) - timedelta(microseconds=1)
    else:  # 1 月
        start = datetime(y - 1, 9, 1)
        end = datetime(y, 3, 1) - timedelta(microseconds=1)
    return start, end


def current_term_label(now: datetime | None = None) -> str:
    now = now or now_local()
    y, m = now.year, now.month
    if m >= 9:
        return f"{y}-{y + 1}学年第一学期"
    if m in (1, 2):
        return f"{y - 1}-{y}学年第一学期"
    return f"{y - 1}-{y}学年第二学期" if m <= 7 else f"{y}-{y + 1}学年第一学期"


def minutes_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 60.0
