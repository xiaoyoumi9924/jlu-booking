import argparse
import getpass
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

if __package__:
    from .api import (
        SPORTS,
        VENUES,
        ServerResponseError,
        resolve_venue_sport,
        book_place,
        can_book,
        extract_available_slots,
        get_companion_user,
        query_courts,
    )
    from .config import AUTO_CONFIG_FILE, DEFAULT_AUTO_CONFIG, load_auto_config, validate_auto_config
    from .paths import LOG_DIR, RUN_STATUS_FILE, RUNTIME_DIR, STATE_DIR, TOKEN_FILE
    from .run_status import RunStatusError, write_run_status
    from .token_store import TokenStoreError, resolve_token, save_token
    from .token_validation import validate_token_online
else:
    # 兼容 `python jlu_booking/auto.py` 这种按文件运行的方式。
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from jlu_booking.api import (
        SPORTS,
        VENUES,
        ServerResponseError,
        resolve_venue_sport,
        book_place,
        can_book,
        extract_available_slots,
        get_companion_user,
        query_courts,
    )
    from jlu_booking.config import AUTO_CONFIG_FILE, DEFAULT_AUTO_CONFIG, load_auto_config, validate_auto_config
    from jlu_booking.paths import LOG_DIR, RUN_STATUS_FILE, RUNTIME_DIR, STATE_DIR, TOKEN_FILE
    from jlu_booking.run_status import RunStatusError, write_run_status
    from jlu_booking.token_store import TokenStoreError, resolve_token, save_token
    from jlu_booking.token_validation import validate_token_online


# ============================================================
# 吉林大学场馆通用自动预约
# ============================================================
# 日常设置由用户配置文件管理。
# 可由任意操作系统的定时任务执行 python -m jlu_booking.auto。
# 如需临时覆盖配置，可使用 --venue / --sport / --day / --companion / --court / --time。
# 命令行参数只影响本次运行，不会改写配置文件。
# ============================================================


# =========================
# 1. 运行时配置（启动时从 JSON + 命令行参数加载）
# =========================

VENUE_NAME = DEFAULT_AUTO_CONFIG["venue"]
SPORT_NAME = DEFAULT_AUTO_CONFIG["sport"]
TARGET_DAY = DEFAULT_AUTO_CONFIG["target_day"]
COMPANION_STUDENT_NUMBER = DEFAULT_AUTO_CONFIG["companion_student_number"]
PREFERRED_COURT_NUMBER = DEFAULT_AUTO_CONFIG["preferred_court_number"]
REAL_BOOKING_ENABLED = DEFAULT_AUTO_CONFIG["real_booking_enabled"]
TIME_PRIORITY = [tuple(item) for item in DEFAULT_AUTO_CONFIG["time_priority"]]

SHOP_NUM, SPORT_SHORT_NAME = resolve_venue_sport(VENUE_NAME, SPORT_NAME)
PREFERRED_COURT_NAME = f"{SPORT_NAME}{PREFERRED_COURT_NUMBER}"

LOG_FILE = LOG_DIR / f"{VENUE_NAME}_{SPORT_NAME}_auto.log"
TIMING_LOG_FILE = LOG_DIR / f"{VENUE_NAME}_{SPORT_NAME}_request_timing.log"


# =========================
# 2. 时间与扫描频率
# =========================

START_TIME = dt_time(7, 27, 0)
CORE_START_TIME = dt_time(7, 29, 57)
STOP_TIME = dt_time(7, 33, 0)
BOOKING_TIMEZONE = ZoneInfo("Asia/Shanghai")

WARMUP_INTERVAL = 1.0
CORE_INTERVAL = 0.1
RATE_LIMIT_INTERVAL = 5.0


class BookingOutcomeUnknown(RuntimeError):
    """最终提交已发出，但客户端无法确认服务器是否已经执行。"""


class CoreWindowEnded(RuntimeError):
    """07:33 后禁止发起新的学校接口请求。"""


@dataclass
class RunStatistics:
    started_at: datetime
    query_count: int = 0
    canbook_count: int = 0
    freebuy_count: int = 0
    not_open_count: int = 0
    target_unavailable_count: int = 0
    server_rejected_count: int = 0
    transport_error_count: int = 0
    http_error_count: int = 0
    stop_reason: str = "UNKNOWN"
    booked_slot: dict | None = None

    def record_request(self, endpoint):
        if endpoint == "query":
            self.query_count += 1
        elif endpoint == "canBook":
            self.canbook_count += 1
        elif endpoint == "freeBuyPlace":
            self.freebuy_count += 1

    def record_error(self, exc):
        category = classify_booking_error(exc)
        if is_http_error(exc):
            self.http_error_count += 1
        elif is_transport_error(exc):
            self.transport_error_count += 1

        if category == "not_open":
            self.not_open_count += 1
        elif category == "target_unavailable":
            self.target_unavailable_count += 1
        elif category == "server_rejected":
            self.server_rejected_count += 1


class RuntimeTokenValidationError(RuntimeError):
    """The effective Token was rejected or could not be checked safely."""

    def __init__(self, status):
        self.status = status
        super().__init__(status)


def configure_text_output(streams=None):
    """Force UTF-8 for the GUI worker and escape any future bad characters."""

    selected_streams = (sys.stdout, sys.stderr) if streams is None else streams
    for stream in selected_streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            # Some test runners and replaced streams cannot be reconfigured.
            continue


def parse_time_range(value):
    text = str(value).strip()
    if "-" not in text:
        raise argparse.ArgumentTypeError(
            f"时间段 {text!r} 格式错误，应写成 HH:MM-HH:MM。"
        )

    start, end = (part.strip() for part in text.split("-", 1))
    try:
        normalized = validate_auto_config(
            {
                **DEFAULT_AUTO_CONFIG,
                "time_priority": [[start, end]],
            }
        )["time_priority"][0]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc

    return normalized


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="吉林大学体育场馆自动预约。默认读取用户配置文件。"
    )
    parser.add_argument(
        "--config",
        default=str(AUTO_CONFIG_FILE),
        help="配置文件路径，默认使用跨平台用户配置目录",
    )
    parser.add_argument("--venue", choices=list(VENUES.keys()), help="临时覆盖预约场馆")
    parser.add_argument("--sport", choices=list(SPORTS.keys()), help="临时覆盖运动项目")
    parser.add_argument("--day", choices=["今天", "明天"], help="临时覆盖预约日期")
    parser.add_argument("--companion", help="临时覆盖同行人学工号")
    parser.add_argument("--court", type=int, help="临时覆盖首选场地编号")
    parser.add_argument(
        "--immediate", action="store_true",
        help="立即开始持续查询；仅供网页单次任务使用，不等待每天预约窗口",
    )
    parser.add_argument(
        "--time",
        action="append",
        type=parse_time_range,
        metavar="HH:MM-HH:MM",
        help="临时覆盖重点时间；可重复使用多次，顺序即优先级",
    )

    booking_mode = parser.add_mutually_exclusive_group()
    booking_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="本次只扫描，不真实提交预约",
    )
    booking_mode.add_argument(
        "--real-booking",
        action="store_true",
        help="本次强制开启真实预约",
    )

    info_mode = parser.add_mutually_exclusive_group()
    info_mode.add_argument(
        "--show-config",
        action="store_true",
        help="用中文摘要显示本次生效配置后退出",
    )
    info_mode.add_argument(
        "--show-config-json",
        action="store_true",
        help="用 JSON 显示本次生效配置后退出",
    )
    info_mode.add_argument(
        "--show-paths",
        action="store_true",
        help="打印配置与运行数据路径后退出",
    )
    return parser


def load_runtime_settings(args, environ=None):
    config_path = Path(args.config).expanduser()
    settings = load_auto_config(config_path, create_if_missing=True)
    environment = os.environ if environ is None else environ

    overrides = {}
    if args.venue is not None:
        overrides["venue"] = args.venue
    if args.sport is not None:
        overrides["sport"] = args.sport
    if args.day is not None:
        overrides["target_day"] = args.day
    if args.companion is not None:
        overrides["companion_student_number"] = args.companion
    elif environment.get("JLU_BOOKING_COMPANION", "").strip():
        overrides["companion_student_number"] = environment[
            "JLU_BOOKING_COMPANION"
        ].strip()
    if args.court is not None:
        overrides["preferred_court_number"] = args.court
    if args.time is not None:
        overrides["time_priority"] = args.time
    if args.dry_run:
        overrides["real_booking_enabled"] = False
    elif args.real_booking:
        overrides["real_booking_enabled"] = True

    merged = dict(settings)
    merged.update(overrides)
    return validate_auto_config(merged), config_path, bool(overrides)


def apply_runtime_settings(settings):
    global VENUE_NAME
    global SPORT_NAME
    global TARGET_DAY
    global COMPANION_STUDENT_NUMBER
    global PREFERRED_COURT_NUMBER
    global REAL_BOOKING_ENABLED
    global TIME_PRIORITY
    global SHOP_NUM
    global SPORT_SHORT_NAME
    global PREFERRED_COURT_NAME
    global LOG_FILE
    global TIMING_LOG_FILE

    VENUE_NAME = settings["venue"]
    SPORT_NAME = settings["sport"]
    TARGET_DAY = settings["target_day"]
    COMPANION_STUDENT_NUMBER = settings["companion_student_number"]
    PREFERRED_COURT_NUMBER = settings["preferred_court_number"]
    REAL_BOOKING_ENABLED = settings["real_booking_enabled"]
    TIME_PRIORITY = [tuple(item) for item in settings["time_priority"]]

    SHOP_NUM, SPORT_SHORT_NAME = resolve_venue_sport(VENUE_NAME, SPORT_NAME)
    PREFERRED_COURT_NAME = f"{SPORT_NAME}{PREFERRED_COURT_NUMBER}"
    LOG_FILE = LOG_DIR / f"{VENUE_NAME}_{SPORT_NAME}_auto.log"
    TIMING_LOG_FILE = LOG_DIR / f"{VENUE_NAME}_{SPORT_NAME}_request_timing.log"

def resolve_target_date():
    """
    根据 TARGET_DAY 返回实际预约日期和显示文字。

    TARGET_DAY = "今天" -> 预约今天
    TARGET_DAY = "明天" -> 预约明天
    """
    today = now_local().date()
    if TARGET_DAY == "今天":
        return today.isoformat(), "今天"

    if TARGET_DAY == "明天":
        return (today + timedelta(days=1)).isoformat(), "明天"

    raise ValueError(
        'TARGET_DAY 只能填写 "今天" 或 "明天"。'
    )


def sanitized_settings(settings):
    """返回可安全显示的配置副本。"""

    display_settings = dict(settings)
    companion = display_settings["companion_student_number"]
    display_settings["companion_student_number"] = (
        f"***{companion[-4:]}" if companion else ""
    )
    return display_settings


def format_settings_summary(settings, config_path, has_overrides=False):
    """生成面向普通用户的中文配置摘要。"""

    query_date, target_day_text = resolve_target_date()
    companion = settings["companion_student_number"]
    if companion:
        companion_text = f"已配置（尾号 {companion[-4:]}）"
    else:
        companion_text = "未配置"

    if settings["real_booking_enabled"]:
        mode_text = "真实预约（发现符合条件的场次后会自动提交）"
    else:
        mode_text = "仅扫描（不会提交预约）"

    source_text = "配置文件 + 命令行临时覆盖" if has_overrides else "配置文件"
    lines = [
        "当前自动预约配置",
        "-" * 56,
        f"配置文件：{config_path}",
        f"配置来源：{source_text}",
        f"预约场馆：{settings['venue']}",
        f"运动项目：{settings['sport']}",
        f"目标日期：{target_day_text}（{query_date}）",
        f"同行人：{companion_text}",
        f"首选场地：{settings['preferred_court_number']} 号场",
        f"运行模式：{mode_text}",
        "重点时间优先级：",
    ]
    lines.extend(
        f"  {index}. {start} - {end}"
        for index, (start, end) in enumerate(settings["time_priority"], start=1)
    )
    lines.append("-" * 56)

    if settings["real_booking_enabled"]:
        lines.append("【注意】真实预约已开启，运行自动任务可能产生真实预约记录。")
    elif not companion:
        lines.append(
            "下一步：请运行 jlu-booking，在左侧“自动预约”中完成设置；"
            "仅测试查询可使用 --dry-run。"
        )
    else:
        lines.append("提示：当前是仅扫描模式，不会提交真实预约。")

    lines.append("如需查看 JSON：jlu-booking-auto --show-config-json")
    return "\n".join(lines)


def now_local():
    return datetime.now(BOOKING_TIMEZONE)


def _as_booking_datetime(value):
    """把自动预约使用的时间统一解释为北京时间。"""

    if value.tzinfo is None:
        return value.replace(tzinfo=BOOKING_TIMEZONE)
    return value.astimezone(BOOKING_TIMEZONE)


def now_text():
    return now_local().strftime("%H:%M:%S.%f")[:-3]


def ensure_request_allowed():
    """阻止自动任务在当天核心窗口结束后发起新请求。"""

    current_time = now_local().time().replace(tzinfo=None)
    if current_time >= STOP_TIME:
        raise CoreWindowEnded("核心预约窗口已结束。")


def log(message):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{now_local().isoformat()} | {message}\n"
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line)


def update_run_status(status, **fields):
    """Persist status without ever interrupting the booking process."""

    try:
        write_run_status(
            status,
            venue=VENUE_NAME,
            sport=SPORT_NAME,
            **fields,
        )
    except (RunStatusError, OSError, ValueError) as exc:
        try:
            log(f"STATUS_WRITE_FAILED | {type(exc).__name__}")
        except OSError:
            pass


def timing_log(request_id, endpoint, elapsed_ms, result):
    """将不含 Token、学号或请求参数的接口耗时写入独立日志。"""

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if isinstance(request_id, int):
        request_label = f"#{request_id:05d}"
    else:
        request_label = str(request_id)
    line = (
        f"{now_local().isoformat()} | {request_label} | "
        f"{endpoint:<13} | {elapsed_ms:7.0f} ms | {result}\n"
    )
    with TIMING_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line)


def timed_api_call(
    request_id,
    endpoint,
    function,
    /,
    *,
    request_guard=None,
    stats=None,
    **kwargs,
):
    """执行一次接口调用，并将耗时与精简结果写入独立日志。"""

    if request_guard is not None:
        request_guard()
    if stats is not None:
        stats.record_request(endpoint)

    started = time.monotonic()
    try:
        result = function(**kwargs)
    except Exception as exc:
        if stats is not None:
            stats.record_error(exc)
        elapsed_ms = (time.monotonic() - started) * 1000
        timing_log(
            request_id,
            endpoint,
            elapsed_ms,
            f"error:{classify_booking_error(exc)}",
        )
        raise

    elapsed_ms = (time.monotonic() - started) * 1000
    timing_log(request_id, endpoint, elapsed_ms, "success")
    return result


def success_state_path(query_date):
    return STATE_DIR / f"success_{query_date}_{VENUE_NAME}_{SPORT_NAME}.json"


def legacy_success_state_path(query_date):
    # 兼容双场馆改造前的状态文件；旧版只支持前卫体育馆。
    return STATE_DIR / f"success_{query_date}_{SPORT_NAME}.json"


def existing_success_state_path(query_date):
    current = success_state_path(query_date)
    if current.exists():
        return current

    legacy = legacy_success_state_path(query_date)
    if VENUE_NAME == "前卫体育馆" and legacy.exists():
        return legacy

    return None


def has_success_state(query_date):
    return existing_success_state_path(query_date) is not None


def save_success_state(query_date, slot):
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "created_at": now_local().isoformat(),
        "venue": VENUE_NAME,
        "sport": SPORT_NAME,
        "date": query_date,
        "court_name": slot["court_name"],
        "place_short_name": slot["place_short_name"],
        "start": slot["start"],
        "end": slot["end"],
    }

    success_state_path(query_date).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def candidate_key(slot):
    """返回单次运行内的候选唯一标识；无效候选返回 None。"""

    place_short_name = str(slot.get("place_short_name", "")).strip()
    start = str(slot.get("start", "")).strip()
    end = str(slot.get("end", "")).strip()
    if not place_short_name or not start or not end:
        return None
    return place_short_name, start, end


def reconcile_invalidated_candidates(invalidated, visible_keys):
    """Advance stale candidates only after a successful query state change.

    ``invalidated`` maps candidate keys to whether the key has disappeared
    from at least one successful query.  A later reappearance releases the
    key.  Query failures never call this helper and therefore cannot release
    a stale candidate accidentally.
    """

    newly_absent = set()
    reappeared = set()

    for key, absence_seen in list(invalidated.items()):
        if key in visible_keys:
            if absence_seen:
                invalidated.pop(key, None)
                reappeared.add(key)
            continue

        if not absence_seen:
            invalidated[key] = True
            newly_absent.add(key)

    return newly_absent, reappeared


def _court_number(court_name):
    matches = re.findall(r"\d+", court_name)
    return int(matches[-1]) if matches else None


def sort_booking_candidates(available_slots):
    """返回不修改输入的、可提交候选稳定排序副本。"""

    time_ranks = {time_range: index for index, time_range in enumerate(TIME_PRIORITY)}

    def sort_key(slot):
        start = str(slot.get("start", "")).strip()
        end = str(slot.get("end", "")).strip()
        time_range = (start, end)
        if time_range in time_ranks:
            time_key = (0, time_ranks[time_range], "", "")
        else:
            time_key = (1, len(time_ranks), start, end)

        court_name = str(slot.get("court_name", "")).strip()
        preferred_rank = 0 if court_name == PREFERRED_COURT_NAME else 1
        court_number = _court_number(court_name)
        if court_number is None:
            court_key = (1, 0)
        else:
            court_key = (0, court_number)

        return (
            *time_key,
            preferred_rank,
            *court_key,
            court_name,
            str(slot.get("place_short_name", "")).strip(),
        )

    valid_slots = [
        slot for slot in available_slots if candidate_key(slot) is not None
    ]
    return sorted(valid_slots, key=sort_key)


def choose_priority_slot(available_slots):
    candidates = sort_booking_candidates(available_slots)
    return candidates[0] if candidates else None


def _error_text(exc):
    if isinstance(exc, ServerResponseError):
        response = exc.result
    else:
        response = str(exc)

    try:
        message = json.dumps(response, ensure_ascii=False)
    except (TypeError, ValueError):
        message = str(response)

    return re.sub(r"\s+", "", message)


def is_daily_booking_limit_error(exc):
    """判断服务器是否明确表示目标日期的预约额度已经用完。"""

    compact = _error_text(exc)
    remaining_is_zero = re.search(
        r"剩余预约次数[：:]0+(?:\.0+)?次?",
        compact,
    )
    mentions_daily_limit = any(
        marker in compact
        for marker in (
            "当天最大预约次数",
            "今日最大预约次数",
            "当天预约次数已达上限",
            "今日预约次数已达上限",
            "已达到当天最大预约次数",
            "已达到今日最大预约次数",
        )
    )
    explicit_limit = any(
        marker in compact
        for marker in (
            "当天预约次数已达上限",
            "今日预约次数已达上限",
            "已达到当天最大预约次数",
            "已达到今日最大预约次数",
        )
    )
    return explicit_limit or bool(mentions_daily_limit and remaining_is_zero)


def is_account_blocked_error(exc):
    """判断服务器是否明确禁止当前账号继续预约。"""

    compact = _error_text(exc).lower()
    return any(
        marker in compact
        for marker in (
            "account_blocked",
            "黑名单",
            "账号异常",
            "账号已被封禁",
            "账号被封禁",
            "账号冻结",
            "请勿使用脚本预定",
        )
    )


def is_booking_window_error(exc):
    """判断预约接口是否因尚未到开放时间而拒绝。"""

    compact = _error_text(exc)
    explicit_window = "请在" in compact and "时间内预约" in compact
    return explicit_window or any(
        marker in compact
        for marker in (
            "未到预约时间",
            "预约时间未到",
            "不在预约时间",
            "预约暂未开始",
            "预约尚未开放",
        )
    )


def is_target_unavailable_error(exc):
    """判断已锁定的场次是否已失效或被其他人预约。"""

    compact = _error_text(exc)
    has_explicit_unavailable_marker = any(
        marker in compact
        for marker in (
            "已被预约",
            "已被预定",
            "已经预约",
            "已有用户预约",
            "下手太晚了",
            "场地已预约",
            "场次已预约",
            "场地已占用",
            "场地已锁定",
            "场地状态变化",
            "场次状态变化",
            "场地不可预约",
            "场次不可预约",
            "该时段不可预约",
            "场次不存在",
        )
    )
    return has_explicit_unavailable_marker


def is_auth_error(exc):
    """判断 Token 或登录状态是否已失效。"""

    compact = _error_text(exc).lower()
    token_is_invalid = "token" in compact and any(
        marker in compact
        for marker in ("失效", "过期", "无效", "错误")
    )
    return token_is_invalid or any(
        marker in compact
        for marker in (
            "登录已失效",
            "登录过期",
            "请先登录",
            "未登录",
        )
    )


def is_rate_limit_error(exc):
    """判断服务器是否要求降低请求频率。"""

    current = exc
    while current is not None:
        compact = _error_text(current).lower()
        response = getattr(current, "response", None)
        if getattr(response, "status_code", None) == 429:
            return True
        if isinstance(current, requests.HTTPError) and "429" in compact:
            return True
        if "429" in compact and (
            "http" in compact or "too many requests" in compact
        ):
            return True
        if any(
            marker in compact
            for marker in (
                "请求过于频繁",
                "操作过于频繁",
                "访问过于频繁",
                "频率限制",
                "请求频率过高",
                "too many requests",
            )
        ):
            return True
        current = current.__cause__
    return False


def is_http_error(exc):
    """判断异常链中是否包含 HTTP 状态错误。"""

    current = exc
    while current is not None:
        if isinstance(current, requests.HTTPError):
            return True
        current = current.__cause__
    return False


def is_transport_error(exc):
    """判断异常是否来自网络传输或响应解析。"""

    current = exc
    while current is not None:
        if isinstance(current, requests.HTTPError):
            return False
        if isinstance(current, requests.RequestException):
            return True
        current = current.__cause__

    text = str(exc)
    return (
        "请求学校服务器失败" in text
        or "服务器返回的数据不是有效 JSON" in text
    )


def classify_booking_error(exc):
    """返回不含服务器原文的稳定错误分类，供状态机与耗时日志使用。"""

    if isinstance(exc, BookingOutcomeUnknown):
        return "submission_unknown"
    if is_account_blocked_error(exc):
        return "account_blocked"
    if is_daily_booking_limit_error(exc):
        return "daily_limit"
    if is_booking_window_error(exc):
        return "not_open"
    if is_target_unavailable_error(exc):
        return "target_unavailable"
    if is_auth_error(exc):
        return "auth"
    if is_rate_limit_error(exc):
        return "rate_limited"
    if is_http_error(exc):
        return "http"
    if is_transport_error(exc):
        return "transport"
    if isinstance(exc, ServerResponseError):
        return "server_rejected"
    return "error"


def report_run_statistics(stats, *, ended_at=None):
    """输出不含凭据和身份信息的单次运行统计。"""

    finished_at = ended_at or now_local()
    print()
    print("=" * 50)
    print("本次自动预约统计")
    print("=" * 50)
    print(f"开始时间：{stats.started_at.strftime('%H:%M:%S')}")
    print(f"高速开始：{CORE_START_TIME.strftime('%H:%M:%S')}")
    print(f"结束时间：{finished_at.strftime('%H:%M:%S')}")
    print()
    print(f"query:             {stats.query_count:5d}")
    print(f"canBook:           {stats.canbook_count:5d}")
    print(f"freeBuyPlace:      {stats.freebuy_count:5d}")
    print()
    print(f"not_open:          {stats.not_open_count:5d}")
    print(f"target_unavailable:{stats.target_unavailable_count:5d}")
    print(f"server_rejected:   {stats.server_rejected_count:5d}")
    print(f"transport_error:   {stats.transport_error_count:5d}")
    print(f"http_error:        {stats.http_error_count:5d}")
    print()
    print(f"STOP_REASON: {stats.stop_reason}")
    if stats.booked_slot is not None:
        print()
        print(f"场地：{stats.booked_slot['court_name']}")
        print(
            f"时间：{stats.booked_slot['start']}-"
            f"{stats.booked_slot['end']}"
        )
    print("=" * 50)


def report_daily_booking_limit(query_date):
    """向用户说明停止原因，避免把已有预约误报成本次预约成功。"""

    print()
    print("=" * 72)
    print("检测到当天预约次数已用完，自动任务已停止")
    print("=" * 72)
    print(f"目标日期：{query_date}")
    print("服务器表明该账号当天已有预约或已经达到预约上限。")
    print("程序不会继续扫描或重复提交；本次不会写入新的预约成功记录。")
    print("=" * 72)


def get_phase(now_dt):
    local_dt = _as_booking_datetime(now_dt)
    t = local_dt.time().replace(tzinfo=None)

    if t < START_TIME:
        return "waiting", None, False

    if START_TIME <= t < CORE_START_TIME:
        return "warmup", WARMUP_INTERVAL, False

    if CORE_START_TIME <= t < STOP_TIME:
        return "core", CORE_INTERVAL, True

    return "finished", None, False


def phase_wait_seconds(now_dt, phase, interval):
    """避免预热等待跨过高速阶段起点。"""

    if phase != "warmup":
        return interval
    local_dt = _as_booking_datetime(now_dt)
    core_start = datetime.combine(
        local_dt.date(),
        CORE_START_TIME,
        tzinfo=BOOKING_TIMEZONE,
    )
    remaining = max(0.0, (core_start - local_dt).total_seconds())
    return min(interval, remaining)


def phase_display_name(phase):
    return {
        "waiting": "等待 07:27",
        "warmup": "预热阶段",
        "core": "核心抢票阶段",
        "immediate": "立即预约",
        "finished": "当天任务结束",
    }[phase]


def wait_until_start():
    while True:
        now_dt = now_local()
        phase, _, _ = get_phase(now_dt)

        if phase != "waiting":
            return

        start_dt = datetime.combine(
            now_dt.date(),
            START_TIME,
            tzinfo=now_dt.tzinfo,
        )

        remaining = max(
            0,
            (start_dt - now_dt).total_seconds(),
        )

        print(
            f"[{now_text()}] 距离 07:27 还有约 {remaining:.0f} 秒，等待启动...",
            flush=True,
        )

        time.sleep(min(30, max(0.01, remaining)))


def attempt_real_booking(
    query_date,
    slot,
    companion_id,
    companion_name,
    token,
    session,
    request_id,
    request_guard=None,
    stats=None,
):
    place_short_name = slot.get("place_short_name")

    if not place_short_name:
        raise RuntimeError("目标场次缺少 place_short_name。")

    timed_api_call(
        request_id,
        "canBook",
        can_book,
        request_guard=request_guard,
        stats=stats,
        query_date=query_date,
        start_time=slot["start"],
        end_time=slot["end"],
        place_short_name=place_short_name,
        shop_num=SHOP_NUM,
        token=token,
        session=session,
    )

    update_run_status("running", target_date=query_date, phase="submitting")
    try:
        result = timed_api_call(
            request_id,
            "freeBuyPlace",
            book_place,
            request_guard=request_guard,
            stats=stats,
            query_date=query_date,
            start_time=slot["start"],
            end_time=slot["end"],
            place_short_name=place_short_name,
            court_name=slot["court_name"],
            companion_user_ids=[companion_id],
            shop_num=SHOP_NUM,
            token=token,
            session=session,
        )
    except CoreWindowEnded:
        raise
    except ServerResponseError:
        # 服务器明确返回失败，上层可以按错误类型决定是否重试。
        update_run_status("running", target_date=query_date, phase="retry")
        raise
    except Exception as exc:
        # 最终提交已发出后断网或响应解析失败时，无法确认服务器
        # 是否已经预约成功。不能盲目重复提交。
        raise BookingOutcomeUnknown(
            "最终预约请求已发出，但无法确认结果。"
        ) from exc

    state_error = None
    try:
        save_success_state(
            query_date=query_date,
            slot=slot,
        )
    except OSError as exc:
        # 服务器已明确预约成功，即使本地状态写入失败也必须停止，
        # 否则可能因为重试而重复提交。
        state_error = exc

    try:
        log(
            f"预约成功 | {query_date} | "
            f"{slot['court_name']} | "
            f"{slot['start']}-{slot['end']}"
        )
    except OSError:
        pass

    print()
    print("=" * 72)
    print(f"预约成功：{SPORT_NAME}")
    print("=" * 72)
    print(f"场地：{slot['court_name']}")
    print(f"日期：{query_date}")
    print(f"时间：{slot['start']} - {slot['end']}")
    print(f"同行人：{companion_name}")
    print(f"服务器返回：{result}")
    print("成功后立即停止当天任务。")
    if state_error is None:
        print(f"状态文件：{success_state_path(query_date)}")
    else:
        print("警告：服务器已确认成功，但本地成功状态写入失败。")
        print(f"写入错误：{state_error}")
        print("请勿立即重新运行，先到学校系统核对预约结果。")
    print("=" * 72)

    return True


def get_runtime_token():
    """Resolve a token for this run without ever printing its value."""

    try:
        token, token_source = resolve_token()
    except TokenStoreError as exc:
        raise SystemExit(str(exc)) from exc

    if not token and sys.stdin.isatty():
        token = getpass.getpass(
            "请输入 JLU_BOOKING_TOKEN（输入不会回显，输入后保存到本机）："
        ).strip()
        if token:
            token_source = "prompt"

    if not token:
        raise SystemExit(
            "没有找到可用 Token。\n"
            "请先运行 jlu-booking-token set 保存 Token，或设置环境变量 "
            "JLU_BOOKING_TOKEN。"
        )

    return token, token_source


def validate_runtime_token(
    token,
    token_source,
    *,
    query_date,
    session,
    request_guard=None,
    stats=None,
):
    """Validate the effective Token and save a prompted value only if valid."""

    result = validate_token_online(
        token,
        query_date=query_date,
        venue_name=VENUE_NAME,
        sport_name=SPORT_NAME,
        session=session,
        request_guard=request_guard,
        request_hook=stats.record_request if stats is not None else None,
        error_hook=stats.record_error if stats is not None else None,
    )
    log(f"TOKEN_CHECK | {result.status}")

    if result.status != "valid":
        raise RuntimeTokenValidationError(result.status)

    if token_source != "prompt":
        return token_source

    try:
        save_token(token)
    except (TokenStoreError, ValueError) as exc:
        print(
            "警告：Token 已验证且本次可使用，但未能保存到本机。"
            f"\n{exc}",
            file=sys.stderr,
        )
        return "prompt"

    return "prompt_saved"


def ensure_auto_run_ready(settings, *, explicit_dry_run=False):
    """Stop an accidental first run before asking for Token or accessing APIs."""

    if settings["companion_student_number"] or explicit_dry_run:
        return

    raise SystemExit(
        "自动预约配置尚未完成：同行人学工号为空。\n"
        f"当前选择：{settings['venue']} / {settings['sport']}。\n\n"
        "请在 GUI 的“自动预约”页填写同行人后直接启动；命令行运行时可用 "
        "--companion 或本次进程的 JLU_BOOKING_COMPANION 环境变量提供。\n"
        "如果只想测试场次查询，可以明确运行：jlu-booking-auto --dry-run"
    )


def _target_text(target):
    return (
        f"{target['court_name']} "
        f"{target['start']}-{target['end']} "
        f"[{target.get('place_short_name', '?')}]"
    )


def report_unknown_booking_outcome(query_date, target):
    """最终提交结果不确定时停止，避免盲目重复预约。"""

    print()
    print("=" * 72)
    print("最终预约结果无法确认，自动任务已停止")
    print("=" * 72)
    print(f"目标日期：{query_date}")
    print(f"目标场次：{_target_text(target)}")
    print("最终提交请求已发出，但响应在返回前出现网络或解析异常。")
    print("程序不会盲目重复提交；请先到学校系统核对结果。")
    print("=" * 72)


def run_booking_loop(
    *,
    query_date,
    companion_id,
    companion_name,
    token,
    session,
    stats=None,
    immediate=False,
):
    """运行预热和基于最新查询结果的核心预约循环。"""

    if stats is None:
        stats = RunStatistics(started_at=now_local())

    request_id = 0
    last_phase = None
    invalidated_candidates = {}

    request_guard = None if immediate else ensure_request_allowed
    while True:
        now_dt = now_local()
        phase, interval, allow_booking = (
            ("immediate", WARMUP_INTERVAL, True) if immediate else get_phase(now_dt)
        )

        if phase == "waiting":
            wait_until_start()
            continue

        if phase == "finished":
            stats.stop_reason = "CORE_WINDOW_ENDED"
            print(
                f"[{now_text()}] 已到当天停止时间，"
                "仍未预约成功，任务结束。",
                flush=True,
            )
            log("当天停止时间到达 | 未预约成功")
            update_run_status(
                "no_result",
                target_date=query_date,
                phase="finished",
            )
            return

        if phase != last_phase:
            print()
            print(
                f"[{now_text()}] >>> 进入阶段："
                f"{phase_display_name(phase)}",
                flush=True,
            )
            if phase == "core":
                print(
                    f"[{now_text()}] >>> 普通路径零人为等待",
                    flush=True,
                )
                log(
                    f"阶段切换 | {phase_display_name(phase)} | "
                    "普通路径零人为等待"
                )
            else:
                print(
                    f"[{now_text()}] >>> 服务器响应后的等待间隔："
                    f"{interval:g} 秒",
                    flush=True,
                )
                log(
                    f"阶段切换 | {phase_display_name(phase)} | "
                    f"服务器响应后的等待间隔 {interval:g} 秒"
                )
            update_run_status(
                "running",
                target_date=query_date,
                phase=phase,
            )

            if phase == "core":
                log("CORE_REFRESH | 丢弃预热候选并重新查询")
            last_phase = phase

        request_id += 1
        try:
            data = timed_api_call(
                request_id,
                "query",
                query_courts,
                request_guard=request_guard,
                stats=stats,
                query_date=query_date,
                sport_short_name=SPORT_SHORT_NAME,
                shop_num=SHOP_NUM,
                token=token,
                session=session,
            )
        except CoreWindowEnded:
            stats.stop_reason = "CORE_WINDOW_ENDED"
            print(
                f"[{now_text()}] 已到 07:33:00，禁止发起新请求，任务结束。",
                flush=True,
            )
            log("核心预约窗口结束 | 未预约成功")
            update_run_status(
                "no_result",
                target_date=query_date,
                phase="finished",
            )
            return
        except Exception as exc:
            category = classify_booking_error(exc)
            print(
                f"[{now_text()}] #{request_id:05d} | 扫描异常 "
                f"[{category}] | {exc}",
                flush=True,
            )
            log(f"扫描异常 | {category} | {type(exc).__name__}: {exc}")
            if is_account_blocked_error(exc):
                stats.stop_reason = "ACCOUNT_BLOCKED"
                print("账号已被明确禁止预约，自动任务已停止。")
                update_run_status(
                    "account_blocked",
                    target_date=query_date,
                    phase=phase,
                )
                return
            if is_daily_booking_limit_error(exc):
                stats.stop_reason = "DAILY_LIMIT"
                report_daily_booking_limit(query_date)
                update_run_status(
                    "daily_limit",
                    target_date=query_date,
                    phase=phase,
                )
                return
            if is_auth_error(exc):
                stats.stop_reason = "AUTH_INVALID"
                print("Token 或登录状态已失效，自动任务已停止。")
                update_run_status(
                    "token_invalid",
                    target_date=query_date,
                    phase=phase,
                )
                return
            if is_rate_limit_error(exc):
                time.sleep(max(RATE_LIMIT_INTERVAL, interval))
            elif phase != "core" or not isinstance(exc, ServerResponseError):
                time.sleep(interval)
            continue

        available_slots = extract_available_slots(data)
        candidates = sort_booking_candidates(available_slots)
        visible_keys = {
            key
            for key in (candidate_key(slot) for slot in candidates)
            if key is not None
        }
        newly_absent, reappeared = reconcile_invalidated_candidates(
            invalidated_candidates,
            visible_keys,
        )
        for key in sorted(newly_absent):
            log(f"INVALIDATED_ABSENT | key={key}")
        for key in sorted(reappeared):
            log(f"REAPPEARED | key={key}")

        eligible = [
            slot
            for slot in candidates
            if candidate_key(slot) not in invalidated_candidates
        ]
        invalidated_count = sum(
            candidate_key(slot) in invalidated_candidates
            for slot in candidates
        )
        target = eligible[0] if eligible else None

        if target is None:
            print(
                f"[{now_text()}] #{request_id:05d} | "
                f"{phase_display_name(phase)} | "
                f"0 个可提交{SPORT_NAME}候选",
                flush=True,
            )
        else:
            print(
                f"[{now_text()}] #{request_id:05d} | "
                f"{phase_display_name(phase)} | "
                f"发现 {len(available_slots)} 个 | "
                f"目标：{_target_text(target)}",
                flush=True,
            )

        if phase == "warmup":
            target_text = _target_text(target) if target is not None else "none"
            log(
                f"WARMUP | visible={len(available_slots)} | "
                f"valid={len(candidates)} | target={target_text}"
            )
            if target is not None:
                print(
                    f"[{now_text()}] 预热阶段只查询不提交；"
                    "候选不会带入核心阶段。",
                    flush=True,
                )
            wait_seconds = phase_wait_seconds(now_local(), phase, interval)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            continue

        if not REAL_BOOKING_ENABLED:
            if target is not None:
                print(
                    f"[{now_text()}] 安全测试模式："
                    "发现目标但不会提交预约。",
                    flush=True,
                )
            if immediate:
                time.sleep(interval)
            continue

        if not allow_booking:
            continue

        log(
            f"QUERY | visible={len(available_slots)} | "
            f"invalidated={invalidated_count} | "
            f"eligible={len(eligible)}"
        )
        if target is None:
            if immediate:
                time.sleep(interval)
            continue

        print(
            f"[{now_text()}] #{request_id:05d} | "
            f"TRY | {_target_text(target)}",
            flush=True,
        )
        log(f"TRY | target={_target_text(target)}")

        try:
            if attempt_real_booking(
                query_date=query_date,
                slot=target,
                companion_id=companion_id,
                companion_name=companion_name,
                token=token,
                session=session,
                request_id=request_id,
                request_guard=request_guard,
                stats=stats,
            ):
                stats.stop_reason = "BOOKING_SUCCESS"
                stats.booked_slot = dict(target)
                log(
                    f"OPEN_DETECTED | result=success | "
                    f"target={_target_text(target)}"
                )
                update_run_status(
                    "success",
                    target_date=query_date,
                    phase=phase,
                )
                return

        except CoreWindowEnded:
            stats.stop_reason = "CORE_WINDOW_ENDED"
            print(
                f"[{now_text()}] 已到 07:33:00，禁止发起新请求，任务结束。",
                flush=True,
            )
            log("核心预约窗口结束 | 未预约成功")
            update_run_status(
                "no_result",
                target_date=query_date,
                phase="finished",
            )
            return

        except BookingOutcomeUnknown:
            stats.stop_reason = "BOOKING_OUTCOME_UNKNOWN"
            report_unknown_booking_outcome(query_date, target)
            log(f"任务停止 | 最终提交结果不确定 | {_target_text(target)}")
            update_run_status(
                "submission_unknown",
                target_date=query_date,
                phase=phase,
            )
            return

        except Exception as exc:
            category = classify_booking_error(exc)

            if is_account_blocked_error(exc):
                stats.stop_reason = "ACCOUNT_BLOCKED"
                print(
                    f"[{now_text()}] 账号已被明确禁止预约，"
                    "自动任务已停止。",
                    flush=True,
                )
                log(f"任务停止 | 账号被禁止预约 | {_target_text(target)}")
                update_run_status(
                    "account_blocked",
                    target_date=query_date,
                    phase=phase,
                )
                return

            if is_daily_booking_limit_error(exc):
                stats.stop_reason = "DAILY_LIMIT"
                report_daily_booking_limit(query_date)
                log(f"任务停止 | 当天预约次数已用完 | {_target_text(target)}")
                update_run_status(
                    "daily_limit",
                    target_date=query_date,
                    phase=phase,
                )
                return

            if is_auth_error(exc):
                stats.stop_reason = "AUTH_INVALID"
                print(
                    f"[{now_text()}] Token 或登录状态已失效，"
                    "自动任务已停止。",
                    flush=True,
                )
                log(f"任务停止 | 登录失效 | {_target_text(target)}")
                update_run_status(
                    "token_invalid",
                    target_date=query_date,
                    phase=phase,
                )
                return

            if is_rate_limit_error(exc):
                backoff = max(RATE_LIMIT_INTERVAL, interval)
                print(
                    f"[{now_text()}] 服务器要求降低频率，"
                    f"等待 {backoff:g} 秒后重新查询。",
                    flush=True,
                )
                log(
                    f"RETRY_REFRESH | category=rate_limited | "
                    f"target={_target_text(target)}"
                )
                time.sleep(backoff)
                continue

            if is_booking_window_error(exc):
                print(
                    f"[{now_text()}] 尚未到服务器实际开放时间；"
                    "立即重新查询最新候选。",
                    flush=True,
                )
                log(
                    f"CORE_RETRY | category=not_open | "
                    f"target={_target_text(target)}"
                )
                if immediate:
                    time.sleep(interval)
                continue

            if is_transport_error(exc):
                print(
                    f"[{now_text()}] 预检网络异常；"
                    f"等待 {interval:g} 秒后重新查询。",
                    flush=True,
                )
                log(
                    f"RETRY_REFRESH | category=transport | "
                    f"target={_target_text(target)}"
                )
                time.sleep(interval)
                continue

            if isinstance(exc, ServerResponseError):
                key = candidate_key(target)
                if category == "target_unavailable" and key is not None:
                    invalidated_candidates[key] = False
                    log(f"INVALIDATED | target={_target_text(target)}")
                    print(
                        f"[{now_text()}] 当前候选明确失效；"
                        "立即重新查询最新状态。",
                        flush=True,
                    )
                    log(
                        "TARGET_FAILED | category=target_unavailable | "
                        f"target={_target_text(target)}"
                    )
                else:
                    print(
                        f"[{now_text()}] 普通服务器拒绝，不改变优先级；"
                        "立即重新查询最新候选。",
                        flush=True,
                    )
                    log(
                        "CORE_RETRY | category=server_rejected | "
                        "priority=unchanged | "
                        f"target={_target_text(target)}"
                    )
                if immediate:
                    time.sleep(interval)
                continue

            print(
                f"[{now_text()}] 预约尝试异常 [{category}]，"
                f"等待 {interval:g} 秒后重新查询 | {exc}",
                flush=True,
            )
            log(
                f"预约尝试异常 | {category} | "
                f"{_target_text(target)} | {type(exc).__name__}: {exc}"
            )
            time.sleep(interval)


def main(argv=None):
    configure_text_output()
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        settings, config_path, has_overrides = load_runtime_settings(args)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"自动预约配置读取失败：{exc}") from exc

    apply_runtime_settings(settings)

    if args.show_config:
        print(
            format_settings_summary(
                settings,
                config_path,
                has_overrides=has_overrides,
            )
        )
        return

    if args.show_config_json:
        print(
            json.dumps(
                sanitized_settings(settings),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.show_paths:
        print(
            json.dumps(
                {
                    "config_file": str(config_path),
                    "runtime_dir": str(RUNTIME_DIR),
                    "log_dir": str(LOG_DIR),
                    "event_log": str(LOG_FILE),
                    "request_timing_log": str(TIMING_LOG_FILE),
                    "state_dir": str(STATE_DIR),
                    "run_status_file": str(RUN_STATUS_FILE),
                    "token_file": str(TOKEN_FILE),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    query_date, target_day_text = resolve_target_date()
    stats = RunStatistics(started_at=now_local())
    update_run_status(
        "starting",
        target_date=query_date,
        phase="startup",
    )

    existing_state = existing_success_state_path(query_date)
    if existing_state is not None:
        stats.stop_reason = "EXISTING_SUCCESS_STATE"
        update_run_status(
            "success",
            target_date=query_date,
            phase="existing_state",
        )
        print(
            f"检测到本地已有{target_day_text}{VENUE_NAME}{SPORT_NAME}预约成功记录，"
            "为避免重复预约，本次直接退出。",
            flush=True,
        )
        print(f"状态文件：{existing_state}")
        report_run_statistics(stats)
        return

    try:
        ensure_auto_run_ready(settings, explicit_dry_run=args.dry_run)
        token, token_source = get_runtime_token()
    except SystemExit:
        stats.stop_reason = "CONFIGURATION_ERROR"
        update_run_status(
            "error",
            target_date=query_date,
            phase="configuration",
        )
        report_run_statistics(stats)
        raise
    session = requests.Session()
    try:
        token_source = validate_runtime_token(
            token,
            token_source,
            query_date=query_date,
            session=session,
            request_guard=None if args.immediate else ensure_request_allowed,
            stats=stats,
        )
    except CoreWindowEnded:
        stats.stop_reason = "CORE_WINDOW_ENDED"
        update_run_status(
            "no_result",
            target_date=query_date,
            phase="finished",
        )
        print("已到 07:33:00，禁止发起新请求，今日自动任务停止。")
        session.close()
        report_run_statistics(stats)
        return
    except RuntimeTokenValidationError as exc:
        if exc.status == "account_blocked":
            stats.stop_reason = "ACCOUNT_BLOCKED"
            update_run_status(
                "account_blocked",
                target_date=query_date,
                phase="token_check",
            )
            print("TOKEN_CHECK | account_blocked")
            print("账号已被明确禁止预约，今日自动任务停止。")
        elif exc.status == "invalid":
            stats.stop_reason = "AUTH_INVALID"
            update_run_status(
                "token_invalid",
                target_date=query_date,
                phase="token_check",
            )
            print("TOKEN_CHECK | invalid")
            print("Token 或登录状态已失效，今日自动任务停止。")
        else:
            stats.stop_reason = "TOKEN_CHECK_UNAVAILABLE"
            update_run_status(
                "network_unavailable",
                target_date=query_date,
                phase="token_check",
            )
            print("TOKEN_CHECK | unavailable")
            print("暂时无法向学校系统验证 Token，今日自动任务停止。")
        session.close()
        report_run_statistics(stats)
        return

    update_run_status(
        "running",
        target_date=query_date,
        phase="startup",
    )

    print("=" * 72)
    print("吉林大学场馆通用自动预约")
    print("=" * 72)
    print(f"配置文件：{config_path}")
    print("配置来源：" + ("配置文件 + 命令行临时覆盖" if has_overrides else "配置文件"))
    token_source_text = {
        "environment": "环境变量 JLU_BOOKING_TOKEN",
        "saved": "本机已保存 Token",
        "prompt": "本次隐藏输入（未保存）",
        "prompt_saved": "本次隐藏输入（已保存）",
    }.get(token_source, "未知")
    print(f"Token 来源：{token_source_text}")
    print(f"场馆：{VENUE_NAME}")
    print(f"shopNum：{SHOP_NUM}")
    print(f"项目：{SPORT_NAME}")
    print(f"接口 shortName：{SPORT_SHORT_NAME}")
    print(f"目标日期：{query_date}（{target_day_text}）")
    print(f"首选场地：{PREFERRED_COURT_NAME}")
    companion_display = (
        f"***{COMPANION_STUDENT_NUMBER[-4:]}"
        if COMPANION_STUDENT_NUMBER
        else "未配置"
    )
    print(f"同行人学工号：{companion_display}")
    print(
        "重点时间："
        + " > ".join(
            f"{start}-{end}"
            for start, end in TIME_PRIORITY
        )
    )
    print(
        f"场地规则：每个重点时间段内优先 {PREFERRED_COURT_NAME}，"
        "没有则选择同时间段其他场地"
    )
    if args.immediate:
        print("运行方式：立即开始，持续查询至成功、明确异常或手动停止")
    else:
        print("自动预约窗口：07:27 预热，07:29:57–07:33 高速预约，07:33 结束")
    print(f"事件日志：{LOG_FILE}")
    print(f"请求耗时日志：{TIMING_LOG_FILE}")
    print(
        "真实预约："
        + ("已开启" if REAL_BOOKING_ENABLED else "未开启")
    )

    if REAL_BOOKING_ENABLED:
        print(
            f"警告：当前为真实预约模式："
            f"发现符合条件的{SPORT_NAME}场次后会自动提交预约"
        )

    print("=" * 72)
    print()

    try:
        companion_id = None
        companion_name = "未配置"
        if REAL_BOOKING_ENABLED:
            print(f"[{now_text()}] 正在验证同行人...", flush=True)

            companion = timed_api_call(
                "startup",
                "companion",
                get_companion_user,
                request_guard=None if args.immediate else ensure_request_allowed,
                stats=stats,
                student_number=COMPANION_STUDENT_NUMBER,
                token=token,
                session=session,
            )

            companion_id = companion.get("id")
            companion_name = companion.get("name", "未知")

            if companion_id is None:
                raise RuntimeError(
                    "同行人查询成功，但服务器没有返回内部用户 ID。"
                )

            print(
                f"[{now_text()}] 同行人验证成功：{companion_name}",
                flush=True,
            )
        else:
            print(
                f"[{now_text()}] 当前仅扫描，不读取或验证同行人信息。",
                flush=True,
            )

        log(
            f"任务启动 | {VENUE_NAME} | {SPORT_NAME} | "
            f"{target_day_text} | {query_date} | "
            f"REAL_BOOKING_ENABLED={REAL_BOOKING_ENABLED}"
        )

        if not args.immediate:
            wait_until_start()
        run_booking_loop(
            query_date=query_date,
            companion_id=companion_id,
            companion_name=companion_name,
            token=token,
            session=session,
            stats=stats,
            immediate=args.immediate,
        )

    except KeyboardInterrupt:
        stats.stop_reason = "USER_INTERRUPTED"
        update_run_status(
            "stopped",
            target_date=query_date,
            phase="interrupted",
        )
        print()
        print("收到 Control+C，程序已停止。")
        log("用户手动停止")
    except Exception as exc:
        if isinstance(exc, CoreWindowEnded):
            status = "no_result"
            stats.stop_reason = "CORE_WINDOW_ENDED"
        elif is_account_blocked_error(exc):
            status = "account_blocked"
            stats.stop_reason = "ACCOUNT_BLOCKED"
        elif is_daily_booking_limit_error(exc):
            status = "daily_limit"
            stats.stop_reason = "DAILY_LIMIT"
        elif is_auth_error(exc):
            status = "token_invalid"
            stats.stop_reason = "AUTH_INVALID"
        elif is_transport_error(exc):
            status = "network_unavailable"
            stats.stop_reason = "NETWORK_UNAVAILABLE"
        else:
            status = "error"
            stats.stop_reason = "UNHANDLED_ERROR"
        update_run_status(
            status,
            target_date=query_date,
            phase="startup",
            detail=type(exc).__name__,
        )
        if status not in {"no_result", "account_blocked", "daily_limit", "token_invalid"}:
            raise
    finally:
        session.close()
        report_run_statistics(stats)


if __name__ == "__main__":
    main()
