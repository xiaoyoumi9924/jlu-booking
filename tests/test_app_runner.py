from jlu_booking.app_runner import build_auto_worker_command


def test_source_auto_worker_command_uses_module_entrypoint():
    assert build_auto_worker_command(
        real_booking_enabled=True,
        executable="python-test",
        frozen=False,
    ) == ["python-test", "-m", "jlu_booking.auto"]


def test_dry_run_is_explicit_when_gui_real_booking_is_disabled():
    assert build_auto_worker_command(
        real_booking_enabled=False,
        executable="python-test",
        frozen=False,
    ) == ["python-test", "-m", "jlu_booking.auto", "--dry-run"]


def test_packaged_auto_worker_reuses_executable():
    assert build_auto_worker_command(
        real_booking_enabled=True,
        executable="JLU Booking.exe",
        frozen=True,
    ) == ["JLU Booking.exe", "--auto-worker"]
