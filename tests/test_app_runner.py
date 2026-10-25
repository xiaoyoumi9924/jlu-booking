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


def test_packaged_auto_worker_uses_console_helper_when_present(tmp_path):
    main_exe = tmp_path / "JLU Booking.exe"
    worker_exe = tmp_path / "JLU Booking Worker.exe"
    main_exe.write_bytes(b"main")
    worker_exe.write_bytes(b"worker")

    assert build_auto_worker_command(
        real_booking_enabled=True,
        executable=str(main_exe),
        frozen=True,
    ) == [str(worker_exe), "--auto-worker"]


def test_source_auto_worker_command_accepts_explicit_module():
    assert build_auto_worker_command(
        real_booking_enabled=False,
        executable="python-web",
        frozen=False,
        module="jlu_booking.auto",
    ) == ["python-web", "-m", "jlu_booking.auto", "--dry-run"]


def test_desktop_start_booking_runs_immediately():
    assert build_auto_worker_command(
        real_booking_enabled=True,
        immediate=True,
        executable="python-test",
        frozen=False,
    ) == ["python-test", "-m", "jlu_booking.auto", "--immediate"]
