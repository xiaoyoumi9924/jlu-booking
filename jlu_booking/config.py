import json
import os
import re
from pathlib import Path

from .api import DEFAULT_VENUE, VENUES, get_sports_for_venue
from .paths import AUTO_CONFIG_FILE

DEFAULT_AUTO_CONFIG = {
    "venue": DEFAULT_VENUE,
    "sport": "羽毛球",
    "target_day": "今天",
    "companion_student_number": "",
    "preferred_court_number": 3,
    "real_booking_enabled": False,
    "time_priority": [
        ["17:30", "19:30"],
        ["15:30", "17:30"],
        ["19:30", "21:30"],
        ["10:00", "12:00"],
    ],
}

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def require_companion_student_number(value):
    """Validate the GUI rule that every saved plan needs a companion."""

    companion = str(value).strip()
    if not companion:
        raise ValueError("同行人学工号为必填项；填写并验证成功后才能保存。")
    return companion


def _validate_time(value, field_name):
    value = str(value).strip()
    if not _TIME_RE.fullmatch(value):
        raise ValueError(f"{field_name}={value!r} 不是合法的 HH:MM 时间。")
    return value


def normalize_time_priority(value):
    if not isinstance(value, list) or not value:
        raise ValueError("time_priority 必须是至少包含一个时间段的列表。")

    normalized = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(
                f"time_priority 第 {index} 项必须是 [开始时间, 结束时间]。"
            )

        start = _validate_time(item[0], f"time_priority[{index}].start")
        end = _validate_time(item[1], f"time_priority[{index}].end")

        if start >= end:
            raise ValueError(
                f"time_priority 第 {index} 项开始时间必须早于结束时间：{start}-{end}"
            )

        pair = [start, end]
        if pair not in normalized:
            normalized.append(pair)

    return normalized


def validate_auto_config(config):
    if not isinstance(config, dict):
        raise ValueError("自动预约配置必须是 JSON 对象。")

    # 兼容旧版配置：旧文件没有 venue 时自动归到前卫体育馆。
    merged = dict(DEFAULT_AUTO_CONFIG)
    merged.update(config)

    venue = str(merged.get("venue", "")).strip()
    if venue not in VENUES:
        raise ValueError(
            f"venue={venue!r} 不受支持。当前支持：{', '.join(VENUES.keys())}"
        )

    sport = str(merged.get("sport", "")).strip()
    venue_sports = get_sports_for_venue(venue)
    if sport not in venue_sports:
        raise ValueError(
            f"{venue} 不支持 sport={sport!r}。"
            f"当前支持：{', '.join(venue_sports.keys())}"
        )

    target_day = str(merged.get("target_day", "")).strip()
    if target_day not in {"今天", "明天"}:
        raise ValueError('target_day 只能是 "今天" 或 "明天"。')

    try:
        preferred_court_number = int(merged.get("preferred_court_number"))
    except (TypeError, ValueError) as exc:
        raise ValueError("preferred_court_number 必须是正整数。") from exc

    if preferred_court_number <= 0:
        raise ValueError("preferred_court_number 必须是正整数。")

    real_booking_enabled = merged.get("real_booking_enabled")
    if not isinstance(real_booking_enabled, bool):
        raise ValueError("real_booking_enabled 必须是 true 或 false。")

    companion = str(merged.get("companion_student_number", "")).strip()
    if real_booking_enabled and not companion:
        raise ValueError(
            "开启真实预约时必须填写同行人学工号。"
            "请先运行 jlu-booking，在左侧打开“自动预约”并完成设置。"
        )

    time_priority = normalize_time_priority(merged.get("time_priority"))

    return {
        "venue": venue,
        "sport": sport,
        "target_day": target_day,
        "companion_student_number": companion,
        "preferred_court_number": preferred_court_number,
        "real_booking_enabled": real_booking_enabled,
        "time_priority": time_priority,
    }


def load_auto_config(path=AUTO_CONFIG_FILE, create_if_missing=True):
    path = Path(path)

    if not path.exists():
        config = validate_auto_config(DEFAULT_AUTO_CONFIG)
        if create_if_missing:
            save_auto_config(config, path)
        return config

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"自动预约配置 JSON 格式错误：{path}\n{exc}"
        ) from exc

    if not isinstance(raw, dict):
        return validate_auto_config(raw)

    return validate_auto_config(raw)


def save_auto_config(config, path=AUTO_CONFIG_FILE):
    path = Path(path)
    normalized = validate_auto_config(config)

    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
    temp_path.replace(path)
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass

    return normalized
