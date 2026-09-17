import json

import pytest

from jlu_booking import run_status, status_cli


def test_run_status_round_trip_is_atomic_and_privacy_safe(tmp_path):
    path = tmp_path / "state" / "last_run.json"

    run_status.write_run_status(
        "running",
        path=path,
        target_date="2026-09-18",
        venue="前卫体育馆",
        sport="羽毛球",
        phase="core",
    )

    payload = run_status.load_run_status(path)
    assert payload["status"] == "running"
    assert payload["target_date"] == "2026-09-18"
    assert payload["phase"] == "core"
    assert "updated_at" in payload
    assert list(path.parent.glob(f".{path.name}.*")) == []


@pytest.mark.parametrize(
    "field",
    ["token", "companion_student_number", "companion_name", "server_result"],
)
def test_run_status_rejects_sensitive_fields(tmp_path, field):
    with pytest.raises(ValueError, match="不允许"):
        run_status.write_run_status(
            "running",
            path=tmp_path / "last_run.json",
            **{field: "private-value"},
        )


def test_status_cli_reports_missing_and_corrupt_state(tmp_path, capsys):
    path = tmp_path / "last_run.json"

    assert status_cli.main([], status_path=path) == 1
    assert "暂无自动任务运行记录" in capsys.readouterr().out

    path.write_text("not-json", encoding="utf-8")
    assert status_cli.main([], status_path=path) == 2
    assert "状态文件损坏" in capsys.readouterr().out


def test_status_cli_prints_human_readable_result_without_private_values(
    tmp_path,
    capsys,
):
    path = tmp_path / "last_run.json"
    path.write_text(
        json.dumps(
            {
                "status": "token_invalid",
                "updated_at": "2026-09-17T07:27:00+08:00",
                "target_date": "2026-09-18",
                "venue": "前卫体育馆",
                "sport": "羽毛球",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert status_cli.main([], status_path=path) == 0
    output = capsys.readouterr().out
    assert "Token 已失效" in output
    assert "2026-09-18" in output
    assert "private-value" not in output
