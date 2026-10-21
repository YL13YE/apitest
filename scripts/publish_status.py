#!/usr/bin/env python3
"""Export public metrics and commit only managed files to a separate site repository."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from apitest.public import FILES


def git(repo, *args, check=True):
    env=dict(os.environ)
    env.pop("GH_DEBUG",None)
    env["GIT_TERMINAL_PROMPT"]="0"
    command=["git"]
    remote=subprocess.run(["git","-C",str(repo),"remote","get-url","origin"],capture_output=True,text=True)
    if remote.stdout.strip().startswith("https://github.com/") and shutil.which("gh"):
        command += ["-c","credential.helper=","-c","credential.helper=!gh auth git-credential"]
    return subprocess.run(command+["-C",str(repo),*args],env=env,check=check,capture_output=True,text=True,timeout=90)


def publish(args):
    repo=Path(args.site_repo).expanduser().resolve()
    if repo==SOURCE or SOURCE in repo.parents:
        raise ValueError("展示仓库必须独立于 APITest 源码目录")
    top=git(repo,"rev-parse","--show-toplevel").stdout.strip()
    if Path(top).resolve()!=repo:
        raise ValueError("site-repo 必须是展示仓库根目录")
    if git(repo,"branch","--show-current").stdout.strip()!="main":
        raise ValueError("展示仓库须在 main 分支")
    staged=set(filter(None,git(repo,"diff","--cached","--name-only","-z").stdout.split("\0")))
    if staged-set(FILES):
        raise ValueError("展示仓库存在非展示文件的暂存修改，已停止")
    lock=Path(git(repo,"rev-parse","--absolute-git-dir").stdout.strip())/"apitest-publish.lock"
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status":"busy"}
        command=[sys.executable,"-m","apitest","export-site","--endpoint",args.endpoint,"--out",str(repo),"--title",args.title]
        if args.history:command += ["--history",str(Path(args.history).expanduser().resolve())]
        if args.models:command += ["--models",args.models]
        result=subprocess.run(command,cwd=SOURCE,check=True,capture_output=True,text=True,timeout=90)
        exported=json.loads(result.stdout)
        git(repo,"add","--",*FILES)
        changed=git(repo,"diff","--cached","--quiet",check=False).returncode
        if changed not in (0,1):raise RuntimeError("无法检查展示文件的修改")
        committed=bool(changed)
        if committed:
            git(repo,"commit","-m","Update public model measurements")
        # Also retry a previous commit whose push failed; never force push.
        git(repo,"push","--porcelain","origin","HEAD:refs/heads/main")
        return {"status":"published" if committed else "unchanged","commit":git(repo,"rev-parse","--short","HEAD").stdout.strip(),
                "changed_files":exported["changed_files"],"last_sample":exported["last_sample"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-repo",required=True)
    parser.add_argument("--endpoint",required=True)
    parser.add_argument("--history")
    parser.add_argument("--models")
    parser.add_argument("--title",default="课题组模型表现")
    parser.add_argument("--interval",type=int,default=600,help="检查间隔秒数，默认600；不调用模型")
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    if args.interval<60:parser.error("检查间隔至少60秒")
    while True:
        try:
            print(json.dumps(publish(args),ensure_ascii=False),flush=True)
        except (ValueError,OSError,subprocess.SubprocessError) as error:
            if isinstance(error,subprocess.CalledProcessError):
                # The subprocess has no API credentials in its arguments. Do not print
                # arbitrary provider data or environment values from subprocess output.
                message="导出、提交或推送失败（退出码 %s）；下次检查会重试"%error.returncode
            else:message=str(error)
            print(json.dumps({"status":"error","message":message},ensure_ascii=False),file=sys.stderr,flush=True)
            if args.once:return 1
        else:
            if args.once:return 0
        time.sleep(args.interval)


if __name__=="__main__":
    raise SystemExit(main())
