from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "tiger_sentence_native"


def wait_for(path: Path, predicate, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            value = path.read_text(encoding="utf-8")
            if predicate(value):
                return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def write_fake_python(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import pathlib
import os
import signal
import sys
import time

log = pathlib.Path(os.environ['MOHU_QWEN35_TEST_LOG'])
model = sys.argv[sys.argv.index('--model') + 1]
with log.open('a', encoding='utf-8') as stream:
    stream.write('start:' + model + '\\n')
    stream.flush()

def stop(signum, frame):
    with log.open('a', encoding='utf-8') as stream:
        stream.write('stop:' + model + '\\n')
        stream.flush()
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while True:
    time.sleep(0.05)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def make_fixture() -> tuple[tempfile.TemporaryDirectory, Path, Path]:
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    native = root / "tiger"
    native.mkdir()
    shutil.copy2(NATIVE / "run_qwen35_scorer.command", native / "run_qwen35_scorer.command")
    shutil.copy2(NATIVE / "scorer_models.zsh", native / "scorer_models.zsh")
    (native / "qwen35_scorer.py").write_text("# unused by fake runtime\n", encoding="utf-8")
    for model in ("Qwen3.5-0.8B-MLX-4bit", "Qwen3-0.6B-4bit"):
        (native / "models" / model).mkdir(parents=True)
    fake_python = root / "fake-python"
    write_fake_python(fake_python)
    log = root / "events.log"
    return temp, native, log


def run_supervisor(native: Path, log: Path, selection: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.update(
        MOHU_QWEN35_PYTHON=str(native.parent / "fake-python"),
        MOHU_SCORER_SELECTION_PATH=str(selection),
        MOHU_SCORER_POLL_INTERVAL="0.05",
        MOHU_QWEN35_TEST_LOG=str(log),
    )
    # The fake runtime receives the event log as its first argument through the
    # test-only command-line hook supported by the supervisor.
    return subprocess.Popen(
        [str(native / "run_qwen35_scorer.command")],
        env=env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_missing_selection_defaults_to_qwen35_without_fallback() -> None:
    temp, native, log = make_fixture()
    try:
        selection = native / "model-selection"
        process = run_supervisor(native, log, selection)
        wait_for(log, lambda value: "Qwen3.5-0.8B-MLX-4bit" in value)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=3)
        assert "Qwen3-0.6B-4bit" not in log.read_text(encoding="utf-8")
    finally:
        temp.cleanup()


def test_selection_change_stops_old_scorer_before_starting_new_one() -> None:
    temp, native, log = make_fixture()
    try:
        selection = native / "model-selection"
        selection.write_text("qwen35-0.8b\n", encoding="utf-8")
        process = run_supervisor(native, log, selection)
        wait_for(log, lambda value: "start:" in value and "Qwen3.5-0.8B-MLX-4bit" in value)
        selection.write_text("qwen3-0.6b\n", encoding="utf-8")
        value = wait_for(
            log,
            lambda content: "stop:" in content
            and "Qwen3.5-0.8B-MLX-4bit" in content
            and "start:" in content
            and "Qwen3-0.6B-4bit" in content,
        )
        assert value.index("stop:") < value.index("start:", value.index("stop:") + 1)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=3)
    finally:
        temp.cleanup()


def test_unknown_selection_is_retried_without_starting_registered_fallback() -> None:
    temp, native, log = make_fixture()
    try:
        selection = native / "model-selection"
        selection.write_text("not-registered\n", encoding="utf-8")
        process = run_supervisor(native, log, selection)
        time.sleep(0.25)
        assert not log.exists() or log.read_text(encoding="utf-8") == ""
        selection.write_text("qwen3-0.6b\n", encoding="utf-8")
        wait_for(log, lambda value: "Qwen3-0.6B-4bit" in value)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=3)
    finally:
        temp.cleanup()


class QwenModelSupervisorTest(unittest.TestCase):
    def test_missing_selection_defaults_to_qwen35_without_fallback(self) -> None:
        test_missing_selection_defaults_to_qwen35_without_fallback()

    def test_selection_change_stops_old_scorer_before_starting_new_one(self) -> None:
        test_selection_change_stops_old_scorer_before_starting_new_one()

    def test_unknown_selection_is_retried_without_starting_registered_fallback(self) -> None:
        test_unknown_selection_is_retried_without_starting_registered_fallback()
