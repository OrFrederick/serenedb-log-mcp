#!/usr/bin/env python3
"""PreToolUse guard for the unattended overnight run.

Reads the hook JSON on stdin. Exits 2 (block, reason on stderr) for:
- deleting paths outside the repo, except temp dirs and caches
- pushing directly to main, or force-pushing without --force-with-lease
- deleting the main branch, the repo, issues or releases
- sudo
Everything else is allowed. Only used by overnight/run.sh, never by daytime sessions.
"""
import json
import os
import shlex
import sys

HOME = os.path.expanduser("~")
REPO = os.environ.get("OVERNIGHT_REPO") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
ALLOWED_DELETE_ROOTS = [
    REPO,
    "/tmp",
    "/private/tmp",
    "/var/folders",
    "/private/var/folders",
    os.environ.get("TMPDIR", "/tmp"),
    f"{HOME}/.cache",
    f"{HOME}/Library/Caches",
    f"{HOME}/.npm/_cacache",
    f"{HOME}/.local/share/uv",
]
OPERATORS = {"&&", "||", ";", "|", "&", "(", ")", "\n"}


def block(reason):
    print(f"overnight guard blocked this command: {reason}", file=sys.stderr)
    sys.exit(2)


def segments(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return [command.split()]
    out, cur = [], []
    for tok in tokens:
        if tok in OPERATORS or set(tok) <= set(";&|()"):
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def under_allowed_root(path):
    real = os.path.realpath(path)
    for root in ALLOWED_DELETE_ROOTS:
        root = os.path.realpath(root)
        if real == root and root != os.path.realpath(REPO):
            return True
        if real.startswith(root.rstrip("/") + "/"):
            return True
    return False


def check_rm(args, cwd):
    paths = [a for a in args if not a.startswith("-")]
    for p in paths:
        if any(ch in p for ch in "$`"):
            block(f"rm target '{p}' uses a variable; write the literal path")
        full = os.path.expanduser(p)
        if not os.path.isabs(full):
            full = os.path.join(cwd, full)
        if os.path.realpath(full) == os.path.realpath(REPO):
            block("rm of the repo root itself")
        if not under_allowed_root(full):
            block(f"rm outside the repo and caches: {p}")


def current_branch(cwd):
    import subprocess
    try:
        return subprocess.run(["git", "-C", cwd, "branch", "--show-current"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def check_git(args, cwd):
    if not args:
        return
    sub = args[0]
    rest = args[1:]
    if sub == "push":
        refs = [a for a in rest if not a.startswith("-")]
        targets = refs[1:] if len(refs) > 1 else []
        implicit = not targets or any(t.split(":")[0] == "HEAD" and ":" not in t for t in targets)
        if implicit and current_branch(cwd) == "main":
            block("push while on main; push your feature branch instead")
        for t in targets:
            dst = t.split(":")[-1].lstrip("+")
            if dst in ("main", "refs/heads/main") or t.startswith(":main"):
                block("direct push to main; merge through a reviewed PR (gh pr merge)")
        if "--delete" in rest and "main" in refs:
            block("deleting main")
        if any(a in ("--force", "-f") or (a.startswith("-") and not a.startswith("--") and "f" in a) for a in rest):
            block("plain force-push; use --force-with-lease on your own branch")
        if any(t.startswith("+") for t in refs):
            block("force refspec '+'; use --force-with-lease on your own branch")
    if sub == "branch" and any(a in ("-D", "-d", "--delete") for a in rest) and "main" in rest:
        block("deleting main")


def check_gh(args):
    if len(args) >= 2 and args[0] in ("repo", "release", "issue", "label") and args[1] == "delete":
        block(f"gh {args[0]} delete")
    if len(args) >= 2 and args[0] == "repo" and args[1] in ("edit", "rename", "archive"):
        block(f"gh repo {args[1]}")
    if args and args[0] == "api" and any(a.upper() in ("-XDELETE", "DELETE") for a in args):
        if not any("/git/refs/heads/" in a and "main" not in a for a in args):
            block("gh api DELETE")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    command = (data.get("tool_input") or {}).get("command", "")
    cwd = data.get("cwd") or os.getcwd()
    for seg in segments(command):
        while seg and ("=" in seg[0] and not seg[0].startswith("-")):
            seg = seg[1:]  # strip VAR=value prefixes
        while seg and seg[0] in ("env", "command", "nohup", "time", "xargs"):
            seg = seg[1:]
        if not seg:
            continue
        prog = os.path.basename(seg[0])
        if prog == "sudo":
            block("sudo")
        if prog == "rm":
            check_rm(seg[1:], cwd)
        if prog == "git":
            args, git_cwd = seg[1:], cwd
            while len(args) >= 2 and args[0] in ("-C", "-c"):
                if args[0] == "-C":
                    git_cwd = os.path.join(git_cwd, os.path.expanduser(args[1]))
                args = args[2:]
            check_git(args, git_cwd)
        if prog == "gh":
            check_gh(seg[1:])
    sys.exit(0)


if __name__ == "__main__":
    main()
