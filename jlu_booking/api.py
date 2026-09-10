import json
import re

import requests


# ============================================================
# 吉林大学场馆接口配置
# ============================================================

BASE_URL = "https://ss.jlu.edu.cn/easyserpClient"

QUERY_URL = (
    f"{BASE_URL}/datediscount/"
    "getPlaceInfoByShortNameDiscount"
)

GET_USER_INFO_URL = (
    f"{BASE_URL}/place/getUserInfo"
)

CAN_BOOK_URL = (
    f"{BASE_URL}/place/canBook"
)

FREE_BUY_PLACE_URL = (
    f"{BASE_URL}/place/freeBuyPlace"
)


# 场馆 -> shopNum -> 可预约运动项目 shortName
#
# 已通过学校系统实际请求确认：
#   前卫体育馆：shopNum=0002
#   宋治平体育馆：shopNum=0001
VENUES = {
    "前卫体育馆": {
        "shop_num": "0002",
        "sports": {
            "羽毛球": "ymq",
            "乒乓球": "ppq",
            "匹克球": "pkq",
        },
    },
    "宋治平体育馆": {
        "shop_num": "0001",
        "sports": {
            "排球": "pq",
            "乒乓球": "ppq",
            "网球": "wq",
        },
    },
}

DEFAULT_VENUE = "前卫体育馆"

# 兼容旧代码中只需要“所有运动项目名称”的场景。
# 实际查询和预约必须同时使用 venue + sport，不能只靠 sport 区分场馆。
SPORTS = {}
for _venue_info in VENUES.values():
    for _sport_name, _short_name in _venue_info["sports"].items():
        SPORTS.setdefault(_sport_name, _short_name)


class ServerResponseError(RuntimeError):
    """学校接口以非 success 状态返回，并保留原始响应供上层判断。"""

    def __init__(self, result):
        self.result = result
        super().__init__(f"服务器返回异常：{result}")


def get_venue_info(venue_name):
    venue_name = str(venue_name).strip()
    try:
        return VENUES[venue_name]
    except KeyError as exc:
        raise ValueError(
            f"venue={venue_name!r} 不受支持。当前支持：{', '.join(VENUES.keys())}"
        ) from exc


def get_sports_for_venue(venue_name):
    return get_venue_info(venue_name)["sports"]


def resolve_venue_sport(venue_name, sport_name):
    venue_info = get_venue_info(venue_name)
    sport_name = str(sport_name).strip()
    sports = venue_info["sports"]

    if sport_name not in sports:
        raise ValueError(
            f"{venue_name} 不支持 sport={sport_name!r}。"
            f"当前支持：{', '.join(sports.keys())}"
        )

    return venue_info["shop_num"], sports[sport_name]


# ============================================================
# 通用辅助函数
# ============================================================

_TOKEN_QUERY_RE = re.compile(r"([?&]token=)[^&\s]+", re.IGNORECASE)


def _redact_sensitive_text(value):
    """避免 requests 异常把 URL 查询参数中的 Token 写进日志。"""

    return _TOKEN_QUERY_RE.sub(r"\1<REDACTED>", str(value))


def _request_json(method, url, *, session=None, **kwargs):
    """统一发送 HTTP 请求并检查 JSON 响应。

    自动任务传入同一个 ``requests.Session`` 时，查询、预检和
    最终提交可以复用同一连接池。GUI 等旧调用方不传入时仍保持
    原有的单次请求行为。
    """

    requester = session if session is not None else requests

    try:
        response = requester.request(
            method,
            url,
            timeout=10,
            **kwargs,
        )

        response.raise_for_status()
        result = response.json()

    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            "学校服务器返回的数据不是有效 JSON"
        ) from exc

    except requests.RequestException as exc:
        # requests 的异常字符串可能包含完整请求 URL，而 URL 中带有 token。
        # 先脱敏再交给上层日志，避免本地日志意外保存凭据。
        safe_message = _redact_sensitive_text(exc)
        raise RuntimeError(
            f"请求学校服务器失败：{safe_message}"
        ) from exc

    if not isinstance(result, dict):
        raise RuntimeError(
            f"服务器返回的数据格式异常：{result}"
        )

    return result


def _check_success(result):
    """检查学校接口返回的 msg 字段。"""

    if result.get("msg") != "success":
        raise ServerResponseError(result)


# ============================================================
# 1. 查询场地
# ============================================================

def query_courts(
    query_date,
    sport_short_name,
    shop_num,
    token,
    session=None,
):
    """查询某一天、某个场馆、某个运动项目的场地状态。"""

    params = {
        "shopNum": str(shop_num),
        "dateymd": query_date,
        "shortName": sport_short_name,
        "token": token,
    }

    result = _request_json(
        "GET",
        QUERY_URL,
        params=params,
        session=session,
    )

    _check_success(result)

    data = result.get("data")

    if not isinstance(data, dict):
        raise RuntimeError(
            f"服务器返回的数据格式异常：{result}"
        )

    return data


def extract_available_slots(data):
    """从查询结果中提取 state == 1 的可预约时段。"""

    available = []

    for place in data.get("placeArray", []):
        place_info = place.get(
            "projectName",
            {},
        )

        court_name = place_info.get(
            "name",
            "未知场地",
        )

        court_id = place_info.get(
            "id",
            "未知",
        )

        # 例如 ppq4 / ymq4 / pkq3 / pq2 / wq1
        place_short_name = place_info.get(
            "shortname",
            "",
        )

        for slot in place.get(
            "projectInfo",
            [],
        ):
            if str(slot.get("state")) == "1":
                available.append(
                    {
                        "court_name": court_name,
                        "court_id": court_id,
                        "place_short_name": place_short_name,
                        "start": slot.get(
                            "starttime",
                            "?",
                        ),
                        "end": slot.get(
                            "endtime",
                            "?",
                        ),
                    }
                )

    return available


# ============================================================
# 2. 查询同行人
# ============================================================

def get_companion_user(
    student_number,
    token,
    session=None,
):
    """根据同行人的学工号查询系统内部用户信息。"""

    params = {
        "token": token,
        "userName": str(
            student_number
        ).strip(),
    }

    result = _request_json(
        "GET",
        GET_USER_INFO_URL,
        params=params,
        session=session,
    )

    _check_success(result)

    data = result.get("data")

    if not isinstance(data, dict):
        raise RuntimeError(
            f"同行人信息格式异常：{result}"
        )

    user_id = data.get("id")

    if user_id is None:
        raise RuntimeError(
            "服务器没有返回同行人的内部用户 ID"
        )

    return data


# ============================================================
# 3. 预约前置检查 canBook
# ============================================================

def can_book(
    query_date,
    start_time,
    end_time,
    place_short_name,
    shop_num,
    token,
    session=None,
):
    """检查目标场地 / 时间段是否允许继续预约。"""

    field_info = [
        {
            "day": query_date,
            "startTime": start_time,
            "endTime": end_time,
            "placeShortName": place_short_name,
        }
    ]

    form_data = {
        "fieldinfo": json.dumps(
            field_info,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "shopNum": str(shop_num),
        "token": token,
    }

    result = _request_json(
        "POST",
        CAN_BOOK_URL,
        data=form_data,
        session=session,
    )

    _check_success(result)

    return result


# ============================================================
# 4. 最终提交预约
# ============================================================

def book_place(
    query_date,
    start_time,
    end_time,
    place_short_name,
    court_name,
    companion_user_ids,
    shop_num,
    token,
    session=None,
):
    """最终创建预约。调用本函数会真实提交预约。"""

    if not companion_user_ids:
        raise ValueError(
            "至少需要一个同行人"
        )

    field_info = [
        {
            "day": query_date,
            "startTime": start_time,
            "endTime": end_time,
            "placeShortName": place_short_name,
            "name": court_name,
        }
    ]

    form_data = {
        "fieldinfo": json.dumps(
            field_info,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "token": token,
        "shopNum": str(shop_num),
        "oldTotal": "0.00",
        "premerother": "",
        "txUserIds": json.dumps(
            companion_user_ids,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }

    result = _request_json(
        "POST",
        FREE_BUY_PLACE_URL,
        data=form_data,
        session=session,
    )

    _check_success(result)

    return result
