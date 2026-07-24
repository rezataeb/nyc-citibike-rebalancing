"""Tests for pipeline/reproduce_all.py.

Focused on this module's own job -- step ordering, the --skip-download
missing-file guard, and command construction -- not on any individual
pipeline step's own logic, which already has its own test suite. Real
subprocess calls are never made here; run_step's underlying subprocess.run
is monkeypatched to a lightweight recorder.
"""

from __future__ import annotations

import sys

import pytest

from pipeline.reproduce_all import STEPS, main, run_step


def test_steps_run_build_full_year_first_and_spot_check_last():
    # The real dependency order: nothing downstream can run before the raw
    # data exists, and the ground-truth check is the last word on whether
    # the whole chain actually produced sane output.
    module_names = [s[0] for s in STEPS]
    assert module_names[0] == "build_full_year"
    assert module_names[-1] == "spot_check"


def test_steps_run_gbfs_logger_before_elasticities():
    # elasticities.py reads live_status.json -- it must be freshly written
    # first, every run, not reused stale (see module docstring).
    module_names = [s[0] for s in STEPS]
    assert module_names.index("gbfs_logger") < module_names.index("elasticities")


def test_run_step_invokes_the_real_module_as_a_subprocess(monkeypatch):
    calls = []
    monkeypatch.setattr("pipeline.reproduce_all.subprocess.run", lambda cmd, check: calls.append((cmd, check)))

    run_step("elasticities", [])

    assert len(calls) == 1
    cmd, check = calls[0]
    assert cmd == [sys.executable, "-m", "pipeline.elasticities"]
    assert check is True


def test_run_step_passes_through_extra_args(monkeypatch):
    calls = []
    monkeypatch.setattr("pipeline.reproduce_all.subprocess.run", lambda cmd, check: calls.append(cmd))

    run_step("gbfs_logger", ["--live"])

    assert calls[0] == [sys.executable, "-m", "pipeline.gbfs_logger", "--live"]


def test_skip_download_fails_loudly_when_committed_data_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.reproduce_all.FLOWS_PATH", tmp_path / "flows.json")
    monkeypatch.setattr("pipeline.reproduce_all.DAILY_NET_FLOW_PATH", tmp_path / "daily_net_flow.parquet")
    monkeypatch.setattr("sys.argv", ["reproduce_all.py", "--skip-download"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert "missing" in str(exc_info.value).lower()


def test_skip_download_omits_build_full_year_when_data_present(tmp_path, monkeypatch):
    flows_path = tmp_path / "flows.json"
    daily_path = tmp_path / "daily_net_flow.parquet"
    flows_path.write_text("{}")
    daily_path.write_bytes(b"")
    monkeypatch.setattr("pipeline.reproduce_all.FLOWS_PATH", flows_path)
    monkeypatch.setattr("pipeline.reproduce_all.DAILY_NET_FLOW_PATH", daily_path)
    monkeypatch.setattr("sys.argv", ["reproduce_all.py", "--skip-download"])

    ran_modules = []
    monkeypatch.setattr("pipeline.reproduce_all.run_step", lambda module, extra_args: ran_modules.append(module) or 0.0)

    main()

    assert "build_full_year" not in ran_modules
    assert ran_modules == [s[0] for s in STEPS if s[0] != "build_full_year"]
