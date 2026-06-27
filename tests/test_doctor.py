"""Tests for i2c/doctor.py — `i2c doctor` environment self-check."""

from __future__ import annotations

import unittest

from i2c import doctor


class TestDoctor(unittest.TestCase):
    def test_run_checks_covers_expected_names(self):
        report = doctor.run_checks()
        names = {c.name for c in report.checks}
        for expected in (
            "i2c on PATH",
            "i2c on login-shell PATH",
            "jsonschema",
            "packaged schemas",
            "toml parser",
            "backend CLI",
            "project .state",
        ):
            self.assertIn(expected, names)

    def test_packaged_schemas_resolve(self):
        # The bundled schemas must always resolve in a working install.
        check = next(c for c in doctor.run_checks().checks if c.name == "packaged schemas")
        self.assertEqual(check.status, doctor.OK)

    def test_report_ok_false_on_any_fail(self):
        report = doctor.DoctorReport(checks=[
            doctor.Check("a", doctor.OK, ""),
            doctor.Check("b", doctor.WARN, ""),
        ])
        self.assertTrue(report.ok())
        report.checks.append(doctor.Check("c", doctor.FAIL, ""))
        self.assertFalse(report.ok())

    def test_path_check_fails_when_absent(self):
        original = doctor.shutil.which
        doctor.shutil.which = lambda name: None  # type: ignore[assignment]
        try:
            check = doctor._check_path()
        finally:
            doctor.shutil.which = original  # type: ignore[assignment]
        self.assertEqual(check.status, doctor.FAIL)
        self.assertTrue(check.remedy)

    def test_path_check_ok_when_present(self):
        original = doctor.shutil.which
        doctor.shutil.which = lambda name: "/usr/local/bin/i2c"  # type: ignore[assignment]
        try:
            check = doctor._check_path()
        finally:
            doctor.shutil.which = original  # type: ignore[assignment]
        self.assertEqual(check.status, doctor.OK)

    def test_backends_warn_when_none(self):
        original = doctor.shutil.which
        doctor.shutil.which = lambda name: None  # type: ignore[assignment]
        try:
            check = doctor._check_backends()
        finally:
            doctor.shutil.which = original  # type: ignore[assignment]
        self.assertEqual(check.status, doctor.WARN)

    def test_login_shell_skipped_without_bash(self):
        original = doctor.shutil.which
        doctor.shutil.which = lambda name: None  # type: ignore[assignment]
        try:
            check = doctor._check_login_shell_path()
        finally:
            doctor.shutil.which = original  # type: ignore[assignment]
        self.assertEqual(check.status, doctor.OK)
        self.assertIn("skipped", check.detail)

    def test_login_shell_ok_when_resolved(self):
        import subprocess as _sp

        orig_which, orig_run, orig_plat = (
            doctor.shutil.which,
            doctor.subprocess.run,
            doctor.sys.platform,
        )
        doctor.sys.platform = "linux"  # type: ignore[assignment]
        doctor.shutil.which = lambda name: "/bin/bash"  # type: ignore[assignment]
        doctor.subprocess.run = lambda *a, **k: _sp.CompletedProcess(  # type: ignore[assignment]
            a, 0, stdout="/usr/local/bin/i2c\n", stderr=""
        )
        try:
            check = doctor._check_login_shell_path()
        finally:
            doctor.shutil.which, doctor.subprocess.run, doctor.sys.platform = (
                orig_which,
                orig_run,
                orig_plat,
            )
        self.assertEqual(check.status, doctor.OK)
        self.assertEqual(check.detail, "/usr/local/bin/i2c")

    def test_login_shell_fails_when_unresolved(self):
        import subprocess as _sp

        orig_which, orig_run, orig_plat = (
            doctor.shutil.which,
            doctor.subprocess.run,
            doctor.sys.platform,
        )
        doctor.sys.platform = "linux"  # type: ignore[assignment]
        doctor.shutil.which = lambda name: "/bin/bash"  # type: ignore[assignment]
        doctor.subprocess.run = lambda *a, **k: _sp.CompletedProcess(  # type: ignore[assignment]
            a, 1, stdout="", stderr=""
        )
        try:
            check = doctor._check_login_shell_path()
        finally:
            doctor.shutil.which, doctor.subprocess.run, doctor.sys.platform = (
                orig_which,
                orig_run,
                orig_plat,
            )
        self.assertEqual(check.status, doctor.FAIL)
        self.assertTrue(check.remedy)


if __name__ == "__main__":
    unittest.main()
