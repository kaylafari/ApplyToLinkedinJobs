import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import resume_build as rb


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "trusted.tex"
        self.source.write_text(r"\documentclass{article}\begin{document}Fixture\end{document}")
        self.evidence = self.root / "form.json"
        self.data = {"job_id": "123", **{k: True for k in rb.REQUIRED},
                     "application_tab": {"id": "7", "kind": "application", "url": "https://example.test/apply/123"}}
        self.evidence.write_text(json.dumps(self.data))
        self.output = self.root / "new-build"

    def fake_runner(self, args, **kwargs):
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        cwd = Path(kwargs["cwd"])
        tool = Path(args[0]).name
        if tool == "latexmk":
            self.assertIn("-no-shell-escape", args)
            self.assertIn("-norc", args)
            (cwd / rb.PDF).write_bytes(b"%PDF-1.7\nMOCK_NOT_REAL_PDF")
        elif tool == "pdftotext":
            (cwd / "resume-text.txt").write_text("Fixture text")
        elif tool == "pdfinfo":
            kwargs["stdout"].write(b"Pages: 2\n")
        elif tool == "pdftoppm":
            for page in (1, 2):
                (cwd / f"resume-page-{page}.png").write_bytes(b"\x89PNG\r\n\x1a\nMOCK")
        return subprocess.CompletedProcess(args, 0)

    def run_build(self, runner=None):
        with patch.object(rb.shutil, "which", side_effect=lambda name: "/mock/" + name):
            return rb.build(self.source, self.output, "123", self.evidence, runner or self.fake_runner)

    def test_success_never_approves_qa(self):
        receipt = self.run_build()
        self.assertEqual(receipt["page_count"], 2)
        self.assertEqual(receipt["status"], "built_pending_review")
        self.assertIs(receipt["qa_approved"], False)
        self.assertEqual(len(receipt["commands"]), 4)
        self.assertFalse((self.output / "Kayla_Farivar_Resume.qa.json").exists())

    def test_invalid_flags_and_job_fail_before_directory(self):
        for key in (*rb.REQUIRED, "job_id"):
            with self.subTest(key=key):
                changed = dict(self.data, **{key: "wrong"})
                self.evidence.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    self.run_build()
                self.assertFalse(self.output.exists())

    def test_bad_tab_rejected(self):
        for tab in ({}, {"id": "7", "kind": "listing", "url": "https://x.test"},
                    {"id": "7", "kind": "application", "url": "file:///tmp/form"}):
            self.evidence.write_text(json.dumps(dict(self.data, application_tab=tab)))
            with self.assertRaises(ValueError):
                self.run_build()

    def test_existing_directory_and_symlink_not_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / rb.PDF
        sentinel.write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            self.run_build()
        self.assertEqual(sentinel.read_bytes(), b"preserve")
        link = self.root / "link"
        link.symlink_to(self.output, target_is_directory=True)
        self.output = link
        with self.assertRaises(FileExistsError):
            self.run_build()

    def test_command_failure_persists_logs(self):
        def fail(args, **kwargs):
            kwargs["stdout"].write(b"compile error")
            return subprocess.CompletedProcess(args, 1)
        with self.assertRaises(RuntimeError):
            self.run_build(fail)
        receipt = json.loads((self.output / "build-receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertFalse(receipt["qa_approved"])
        self.assertIn("compile error", (self.output / "command-1-latexmk.log").read_text())

    def test_timeout_persists_failure(self):
        def timeout(args, **kwargs):
            raise subprocess.TimeoutExpired(args, 180)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_build(timeout)
        self.assertEqual(json.loads((self.output / "build-receipt.json").read_text())["status"], "failed")

    def test_missing_render_fails(self):
        def missing(args, **kwargs):
            result = self.fake_runner(args, **kwargs)
            if Path(args[0]).name == "pdftoppm":
                (Path(kwargs["cwd"]) / "resume-page-2.png").unlink()
            return result
        with self.assertRaisesRegex(RuntimeError, "Rendered page"):
            self.run_build(missing)

    def test_empty_text_fails(self):
        def empty(args, **kwargs):
            result = self.fake_runner(args, **kwargs)
            if Path(args[0]).name == "pdftotext":
                (Path(kwargs["cwd"]) / "resume-text.txt").write_text("")
            return result
        with self.assertRaisesRegex(RuntimeError, "Text extraction"):
            self.run_build(empty)

    def test_missing_tool_records_failure(self):
        with patch.object(rb.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                rb.build(self.source, self.output, "123", self.evidence)
        self.assertEqual(json.loads((self.output / "build-receipt.json").read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
