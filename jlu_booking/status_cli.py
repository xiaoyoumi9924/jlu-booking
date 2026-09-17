"""Display the privacy-safe status of the latest automatic task."""

from __future__ import annotations

import argparse

from .paths import RUN_STATUS_FILE
from .run_status import RunStatusError, load_run_status


STATUS_TEXT = {
    "starting": "正在启动",
    "running": "正在运行",
    "success": "预约成功",
    "no_result": "当日未预约成功",
    "token_invalid": "Token 已失效",
    "network_unavailable": "学校服务器或网络暂时不可用",
    "daily_limit": "当日预约次数已用完",
    "submission_unknown": "最终提交结果无法确认",
    "stopped": "任务已停止",
    "error": "任务异常结束",
}


def build_arg_parser():
    return argparse.ArgumentParser(description="查看最近一次自动预约任务状态。")


def main(argv=None, *, status_path=RUN_STATUS_FILE):
    build_arg_parser().parse_args(argv)
    try:
        payload = load_run_status(status_path)
    except RunStatusError as exc:
        print(exc)
        return 2
    if payload is None:
        print("暂无自动任务运行记录。")
        return 1

    print(f"当前状态：{STATUS_TEXT[payload['status']]}")
    print(f"更新时间：{payload.get('updated_at', '未知')}")
    if payload.get("target_date"):
        print(f"目标日期：{payload['target_date']}")
    if payload.get("venue") or payload.get("sport"):
        print(
            "预约项目："
            f"{payload.get('venue', '未知')} · {payload.get('sport', '未知')}"
        )
    if payload.get("phase"):
        print(f"运行阶段：{payload['phase']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

