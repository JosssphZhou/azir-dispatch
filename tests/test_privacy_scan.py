import codecs
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCANNER = Path(__file__).resolve().parents[1] / "scripts" / "privacy-scan.py"
# Construct deliberately private test data only inside disposable repositories.
PRIVATE_TEXT = "\n".join([
    "fake.person" + "@" + "example.invalid",
    "/" + "Users/" + "someone/project",
    "zh" + "ousefu", "YI" + "QI", "X" + "IE",
])
BOM_ENCODINGS = [(codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"),
                 (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be")]
# Synthetic value; the full token is never present in tracked source or output.
SYNTHETIC_KEY = "gh" + "p_" + "7kN2xQ9mR4vT8cY6wE3pH1jL5sB0dF7gA9zU"



class PrivacyScanTests(unittest.TestCase):
    def test_public_repository_account_is_allowed_only_as_an_exact_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.git(repo, "init", "-q", "-b", "main")
            path = repo / "README.md"
            path.write_text("https://github.com/JosssphZhou/azir-dispatch\n")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "新增：公开仓库链接")
            clean = self.scan(repo)
            self.assertEqual(clean.returncode, 0, clean.stdout)

            path.write_text("x" + "JosssphZhou" + "x\n")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "更新：模糊身份标记")
            flagged = self.scan(repo)
            self.assertEqual(flagged.returncode, 1, flagged.stdout)
            self.assertIn("工作树/README.md:1 [隐私标记", flagged.stdout)

    def test_four_bom_files_containing_only_keys_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.git(repo, "init", "-q", "-b", "main")
            text = "github_token=" + SYNTHETIC_KEY + "\n"
            for bom, encoding in BOM_ENCODINGS:
                (repo / (encoding + ".txt")).write_bytes(bom + text.encode(encoding))
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "新增：合成密钥样例")
            result = self.scan(repo)
            self.assertEqual(result.returncode, 1, result.stdout)
            for _, encoding in BOM_ENCODINGS:
                self.assertIn(encoding + ".txt:1 [github-pat]", result.stdout)
            self.assertIn("密钥/decoded/-/工作树/", result.stdout)
            self.assertIn("密钥/decoded/-/历史/", result.stdout)
            self.assertNotIn(SYNTHETIC_KEY, result.stdout)

    def test_same_keys_in_utf8_control_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.git(repo, "init", "-q", "-b", "main")
            for _, encoding in BOM_ENCODINGS:
                (repo / (encoding + ".txt")).write_text("github_token=" + SYNTHETIC_KEY + "\n", encoding="utf-8")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "新增：UTF-8 密钥对照")
            result = self.scan(repo)
            self.assertEqual(result.returncode, 1, result.stdout)
            for _, encoding in BOM_ENCODINGS:
                self.assertIn(encoding + ".txt:1 [github-pat]", result.stdout)
            self.assertIn("密钥/dir/", result.stdout)
            self.assertNotIn(SYNTHETIC_KEY, result.stdout)

    def test_keys_only_in_bom_history_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.git(repo, "init", "-q", "-b", "main")
            for bom, encoding in BOM_ENCODINGS:
                (repo / (encoding + ".txt")).write_bytes(bom + ("github_token=" + SYNTHETIC_KEY + "\n").encode(encoding))
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "新增：历史 BOM 密钥")
            for _, encoding in BOM_ENCODINGS:
                (repo / (encoding + ".txt")).unlink()
            (repo / "public.txt").write_text("public text\n", encoding="utf-8")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "更新：干净工作树")
            result = self.scan(repo)
            self.assertEqual(result.returncode, 1, result.stdout)
            for _, encoding in BOM_ENCODINGS:
                self.assertIn(encoding + ".txt:1 [github-pat]", result.stdout)
            self.assertIn("密钥/decoded/-/历史/", result.stdout)
            self.assertIn("gitleaks git exit=0", result.stdout)
            self.assertIn("gitleaks dir exit=0", result.stdout)
            self.assertNotIn("密钥/decoded/-/工作树/", result.stdout)
            self.assertNotIn(SYNTHETIC_KEY, result.stdout)

    def git(self, repo, *args):
        return subprocess.run([
            "git", "-C", str(repo), "-c", "core.hooksPath=/dev/null",
            "-c", "commit.gpgsign=false", "-c", "user.name=JosssphZhou",
            "-c", "user.email=260232027+JosssphZhou@users.noreply.github.com",
            *args,
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def scan(self, repo, extra_env=None):
        env = {k: v for k, v in os.environ.items() if k != "AZIR_PRIVACY_EXTRA_AUTHORS"}
        env.update(extra_env or {})
        return subprocess.run([sys.executable, str(SCANNER), str(repo)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)

    def test_extra_author_names_come_from_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.git(repo, "init", "-q", "-b", "main")
            (repo / "note.txt").write_text("public text\n", encoding="utf-8")
            self.git(repo, "add", ".")
            self.git(repo, "-c", "user.name=Example Author", "commit", "-qm", "新增：样例")
            blocked = self.scan(repo)
            self.assertEqual(blocked.returncode, 1, blocked.stdout)
            self.assertIn("[非公开署名]", blocked.stdout)
            allowed = self.scan(repo, {"AZIR_PRIVACY_EXTRA_AUTHORS": "Other Name, Example Author"})
            self.assertEqual(allowed.returncode, 0, allowed.stdout)

    def test_bom_exports_are_checked_in_worktree_and_history(self):
        encodings = [(codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"),
                     (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be")]
        for bom, encoding in encodings:
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as directory:
                self.check_private_export(Path(directory), bom + PRIVATE_TEXT.encode(encoding))

    def check_private_export(self, repo, data):
        self.git(repo, "init", "-q", "-b", "main")
        path = repo / "windows-export.txt"
        path.write_bytes(data)
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "新增：导出样例")
        current = self.scan(repo)
        self.assertEqual(current.returncode, 1, current.stdout)
        self.assertIn("工作树/windows-export.txt", current.stdout)
        for rule in ("[邮箱]", "[本机路径]", "[隐私标记 1]", "[隐私标记 10]", "[隐私标记 11]"):
            self.assertIn(rule, current.stdout)
        path.write_text("public text\n", encoding="utf-8")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "更新：干净文本")
        historical = self.scan(repo)
        self.assertEqual(historical.returncode, 1, historical.stdout)
        self.assertIn("命中 历史/", historical.stdout)
        self.assertNotIn("命中 工作树/", historical.stdout)

    def test_clean_bom_exports_and_allowed_emails_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.git(repo, "init", "-q", "-b", "main")
            text = "\n".join(["public text", "noreply@anthropic.com", ""])
            for bom, encoding in [(codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"),
                                  (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be")]:
                (repo / (encoding + ".txt")).write_bytes(bom + text.encode(encoding))
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "新增：干净导出")
            result = self.scan(repo)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("未检查：", result.stdout)

    def test_binary_files_are_listed_and_block_even_when_only_in_history(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.git(repo, "init", "-q", "-b", "main")
            for name, data in [("binary.dat", b"\xff\x00\x80"), ("controls.dat", b"public\x00text")]:
                (repo / name).write_bytes(data)
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "新增：二进制样例")
            result = self.scan(repo)
            self.assertEqual(result.returncode, 1, result.stdout)
            for name in ("binary.dat", "controls.dat"):
                self.assertIn("未检查：工作树/" + name, result.stdout)
                (repo / name).unlink()
            (repo / "public.txt").write_text("public text\n")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "更新：仅保留文本")
            result = self.scan(repo)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("未检查：历史/", result.stdout)
            self.assertNotIn("未检查：工作树/", result.stdout)

    def test_malformed_bom_text_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.git(repo, "init", "-q", "-b", "main")
            (repo / "broken.txt").write_bytes(codecs.BOM_UTF16_LE + b"x")
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", "新增：损坏的导出")
            result = self.scan(repo)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("未检查：工作树/broken.txt", result.stdout)


if __name__ == "__main__":
    unittest.main()
