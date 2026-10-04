#!/usr/bin/env python3
"""Read-only release scan. Exit 0: clean, 1: findings, 2: incomplete scan."""

import argparse
import codecs
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


PUBLIC_ACCOUNTS = {"JosssphZhou"}
PUBLIC_ACCOUNT = "JosssphZhou"
PUBLIC_EMAIL = "260232027+" + PUBLIC_ACCOUNT + "@users.noreply.github.com"
ALLOWED_EMAILS = {PUBLIC_EMAIL, "noreply@anthropic.com"}
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LOCAL_PATH = re.compile(
    r"/(?:" + "Users|Volumes|home" + r")/[^\s\"'<>]+"
    r"|/(?:private/)?var/folders/[^\s\"'<>]+"
    r"|[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\s\"'<>]+",
    re.IGNORECASE,
)
# Assemble literal markers so the rule definitions do not flag themselves.
MARKERS = [
    ("zh", "ousefu"), ("Jose", "ph"), ("Zh", "ou"), ("key", "chain"),
    ("security find-", "generic-password"), ("linear", ".app"),
    ("open", "my"), ("stack", "chan"), ("gro", "ki"),
    ("YI", "QI"), ("X", "IE"),
]
RULES = [(f"隐私标记 {i}", re.compile(re.escape("".join(parts)), re.IGNORECASE))
         for i, parts in enumerate(MARKERS, 1)]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def masked_public_identity(text):
    """Only the exact public account and allowed emails are exempt."""
    text = EMAIL.sub(lambda m: " " * len(m[0]) if m[0] in ALLOWED_EMAILS else m[0], text)
    accounts = "|".join(re.escape(account) for account in sorted(PUBLIC_ACCOUNTS, key=len, reverse=True))
    account = re.compile(r"(?<![A-Za-z0-9._%+@\-])(?:" + accounts
                         + r")(?![A-Za-z0-9._%+@\-])")
    return account.sub(lambda m: " " * len(m[0]), text)


def inspect_text(location, data, decoded=None):
    wide_text = data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE,
                                 codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
    try:
        # UTF-32 LE shares the UTF-16 LE prefix; check the longer BOM first.
        if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            text = data.decode("utf-32")
        elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
        if any((ord(ch) < 32 and ch not in "\t\n\r\f") or ch == "\x7f" for ch in text):
            raise UnicodeError("binary control characters")
    except UnicodeError:
        print(f"未检查：{location}")
        return 1
    count = 0
    if wide_text:
        try:
            if decoded is None:
                raise OSError("no decoded snapshot")
            directory, sources = decoded
            name = f"{len(sources)}.txt"
            (directory / name).write_text(text, encoding="utf-8")
            sources[name] = location
        except (OSError, UnicodeError):
            print(f"密钥未检查：{location}")
            count += 1
    text = masked_public_identity(text)
    for label, pattern in [("本机路径", LOCAL_PATH), ("邮箱", EMAIL), *RULES]:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            print(f"命中 {location}:{line} [{label}]")
            count += 1
    return count


def scan_worktree(repo, snapshot, decoded):
    paths = [p for p in git(repo, "ls-files", "-z").split(b"\0") if p]
    findings = 0
    for raw in paths:
        name = os.fsdecode(raw)
        path = repo / name
        findings += inspect_text("工作树文件名", raw)
        if path.is_symlink():
            data = os.fsencode(os.readlink(path))
        elif path.is_file():
            data = path.read_bytes()
        elif not path.exists():
            # A tracked deletion has no current contents; history is still scanned.
            continue
        else:
            raise ValueError("unsupported tracked entry")
        findings += inspect_text(f"工作树/{name}", data, decoded)
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    print(f"当前工作树：{len(paths)} 个已跟踪路径")
    return findings


def scan_history(repo, decoded):
    commits = git(repo, "rev-list", "--all").decode("ascii").splitlines()
    seen_blobs = set()
    findings = 0
    for commit in commits:
        metadata = git(repo, "show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", commit)
        author, author_email, committer, committer_email, message = metadata.split(b"\0", 4)
        for label, name, email in [("作者", author, author_email),
                                   ("提交者", committer, committer_email)]:
            findings += inspect_text(f"历史/{commit}/{label}", name + b"\n" + email)
            if name.decode("utf-8", "replace") not in (*PUBLIC_ACCOUNTS, "周瑟夫"):
                print(f"命中 历史/{commit}/{label}:1 [非公开署名]")
                findings += 1
        findings += inspect_text(f"历史/{commit}/提交说明", message)
        for entry in git(repo, "ls-tree", "-r", "-z", commit).split(b"\0"):
            if not entry:
                continue
            header, name = entry.split(b"\t", 1)
            mode, kind, oid = header.split()
            findings += inspect_text(f"历史/{commit}/文件名", name)
            if kind != b"blob":
                raise ValueError("submodule contents require a separate scan")
            if oid in seen_blobs:
                continue
            seen_blobs.add(oid)
            findings += inspect_text(f"历史/{commit}/{os.fsdecode(name)}",
                                     git(repo, "cat-file", "blob", oid.decode("ascii")), decoded)
    print(f"全部引用可达历史：{len(commits)} 个提交，{len(seen_blobs)} 个不同文件对象")
    return findings


def scan_secrets(repo, snapshot, temporary, decoded):
    decoded_dir, sources = decoded
    if not shutil.which("gitleaks"):
        if sources:
            for location in sources.values():
                print(f"密钥未检查：{location}")
            return len(sources)
        raise ValueError("gitleaks is required")
    # Use built-in rules, regardless of local config, ignore files or allow comments.
    config = temporary / "rules.toml"
    config.write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
    ignore = temporary / "empty-ignore"
    ignore.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("GITLEAKS_CONFIG", "GITLEAKS_CONFIG_TOML")}
    findings = 0
    targets = [("git", repo), ("dir", snapshot)]
    if sources:
        targets.append(("decoded", decoded_dir))
    for mode, target in targets:
        report = temporary / f"{mode}.json"
        tool_mode = "git" if mode == "git" else "dir"
        command = ["gitleaks", tool_mode, "--redact", "--no-banner", "--no-color",
                   "--config", str(config), "--gitleaks-ignore-path", str(ignore),
                   "--ignore-gitleaks-allow", "--report-format", "json",
                   "--report-path", str(report)]
        if mode == "git":
            command.append("--log-opts=--all --full-history")
        command.append(str(target))
        print(f"$ gitleaks {tool_mode} --redact ({mode}，内置规则，忽略豁免，JSON 报告)")
        try:
            result = subprocess.run(command, cwd=temporary, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
            print(f"gitleaks {mode} exit={result.returncode}")
            if result.returncode not in (0, 1) or not report.is_file():
                raise ValueError("gitleaks did not complete")
            leaks = json.loads(report.read_text(encoding="utf-8")) or []
        except (OSError, ValueError):
            if mode != "decoded":
                raise
            for location in sources.values():
                print(f"密钥未检查：{location}")
            findings += len(sources)
            continue
        for leak in leaks:
            name = leak["File"]
            if mode != "git":
                name = str(Path(name).relative_to(target)) if Path(name).is_absolute() else name
            if mode == "decoded":
                name = sources[name]
            print(f"命中 密钥/{mode}/{leak.get('Commit') or '-'}/"
                  f"{name}:{leak['StartLine']} [{leak['RuleID']}]")
        findings += max(len(leaks), int(result.returncode == 1))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="?", default=".", help="repository or worktree path")
    args = parser.parse_args(argv)
    try:
        repo = Path(os.fsdecode(git(Path(args.repo), "rev-parse", "--show-toplevel")).strip())
        if git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true":
            raise ValueError("shallow history cannot be fully scanned")
        print("扫描范围：当前已跟踪文件、全部引用可达历史、提交说明和身份元数据")
        print("历史相同文件对象只检查一次；不读取不可达对象，不修改仓库")
        with tempfile.TemporaryDirectory(prefix="azir-privacy-") as directory:
            temporary = Path(directory)
            snapshot = temporary / "tracked"
            snapshot.mkdir()
            decoded_dir = temporary / "decoded"
            decoded_dir.mkdir()
            decoded = (decoded_dir, {})
            findings = scan_worktree(repo, snapshot, decoded)
            findings += scan_history(repo, decoded)
            findings += scan_secrets(repo, snapshot, temporary, decoded)
        print(f"扫描完成：{findings} 项命中")
        return 1 if findings else 0
    except (OSError, ValueError, subprocess.CalledProcessError, KeyError) as exc:
        # Do not echo command arguments, configuration or possible secret contents.
        print(f"扫描未完成：{type(exc).__name__}；检查仓库完整性、文件权限和 gitleaks", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
