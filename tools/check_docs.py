"""文档体检：校验 md 里引用的本地文件 / `python -m` 模块 / make 目标是否真实存在。

用法（在 macos-pc 下）：
    python3 -m tools.check_docs            # 检查两个仓库 + 根目录的 md
    python3 -m tools.check_docs --list     # 连通过项一起打印
    python3 -m tools.check_docs --paths ../raspberry-pi/hardware/README.md

判据刻意保守，只报「几乎肯定是笔误」的引用：
- 路径在候选根（文档目录、所属仓库根、两个仓库根、工作区根）里都找不到，
  按 basename 在整棵树里也找不到，且它不是运行时产物；
- `python -m X` 里的 X 在两个仓库里都没有对应 .py/包（stdlib 自动放过）；
- `make X` 里的 X 不在 macos-pc/Makefile 的目标里。

运行时产物（录制、标定、报告、渲染输出）写在 RUNTIME_PATTERNS 里，不算错。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOST_ROOT = os.path.dirname(HERE)                    # macos-pc/
WORKSPACE = os.path.dirname(HOST_ROOT)               # 深度/
EDGE_ROOT = os.path.join(WORKSPACE, "raspberry-pi")
REPO_ROOTS = (HOST_ROOT, EDGE_ROOT)
CANDIDATE_ROOTS = (WORKSPACE, HOST_ROOT, EDGE_ROOT)

RUNTIME_PATTERNS = (
    # artifacts/ 不属于任何 git 仓库（里面有标定板 PDF、镜像 tar 等），
    # 新机器 clone 两个仓库后必然没有；文档引用它不算错。
    r"^artifacts/",
    r"(^|/)pairs\.json$",
    r"(^|/)capture\.json$",
    r"^runs/",
    r"^data/recordings/",
    r"^data/calib/",
    r"(^|/)report\.md$",
    r"(^|/)calib/",
)

# 文档里提到、但需要自己导出才有的模型产物（仓库不附带）
ALLOW_EXACT = {
    "models/ffs.onnx",
    "models/yolov8n.engine",
    "models/yolov8n.mlpackage",
}

# 逐日记录类文档：写的是当时做过的事，路径允许已不存在
PATH_SKIP = {"docs/SESSION_CONTEXT.md"}
RUNTIME_EXT = (".json", ".npz", ".jpg", ".jpeg", ".png", ".pdf", ".ply", ".engine", ".log")

KNOWN_PREFIXES = (
    "docs/", "tools/", "hardware/", "stl/", "pi/", "pc/", "shared/", "tests/",
    "docker/", "configs/", "models/", "data/", "runs/", "artifacts/", "scripts/",
    "../raspberry-pi/", "../macos-pc/",
)
EXT = (".py", ".md", ".scad", ".stl", ".txt", ".yaml", ".yml", ".toml", ".ini",
       ".cfg", ".sh", ".pdf", ".json", ".npz", ".csv", ".service", ".xml")

BARE_EXT = (".py", ".scad", ".md", ".yaml", ".yml", ".sh", ".txt")
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "runs", "data", "artifacts"}

CODE_SPAN = re.compile(r"`([^`\n]{1,200})`")
PATHISH = re.compile(r"^[\w.@+~/-]+$")
CJK = re.compile(r"[\u4e00-\u9fff]")
MODULE_CMD = re.compile(r"python3?\s+-m\s+([A-Za-z_][\w.]*)")
MAKE_CMD = re.compile(r"\bmake\s+([A-Za-z][\w.-]*)")
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {
    "unittest", "venv", "pip", "ensurepip", "site", "pydoc", "json", "timeit",
}


def iter_md(paths):
    for p in paths:
        if os.path.isfile(p) and p.endswith(".md"):
            yield p
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in sorted(filenames):
                if name.endswith(".md"):
                    yield os.path.join(dirpath, name)


def build_index():
    """basename -> 若干绝对路径，用来兜底「文档里只写文件名」的写法。"""
    idx = {}
    for root in (WORKSPACE, HOST_ROOT, EDGE_ROOT):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                idx.setdefault(name, []).append(os.path.join(dirpath, name))
    return idx


def candidate_roots(doc_path):
    roots = [os.path.dirname(doc_path)]
    d = os.path.dirname(os.path.abspath(doc_path))
    while True:
        if d in CANDIDATE_ROOTS or d == os.path.dirname(d):
            roots.append(d)
            break
        d = os.path.dirname(d)
    roots.extend(CANDIDATE_ROOTS)
    seen, out = set(), []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def is_runtime(rel):
    rel = rel.replace(os.sep, "/").lstrip("./")
    for pat in RUNTIME_PATTERNS:
        if re.search(pat, rel):
            return True
    return rel.endswith(RUNTIME_EXT) and "/" not in rel and False


def looks_like_path(tok):
    tok = tok.strip()
    if not tok or len(tok) > 160 or " " in tok:
        return False
    if tok.startswith(("http://", "https://", "git@", "-", "tcp://")) or tok.startswith("/"):
        return False
    if any(c in tok for c in "*<>$|{}()[],"):
        return False
    if not PATHISH.match(tok) or CJK.search(tok):
        return False
    if tok.startswith(".") or tok.strip(".") == "":
        return False
    if tok.endswith("/"):                    # 目录写法：只认已知前缀
        return tok.startswith(KNOWN_PREFIXES)
    if "/" in tok:
        # 参数对（lens_dx/lens_dz）、ROS 类型（sensor_msgs/Image）、git ref（origin/main）
        # 都不带扩展名，也不是已知目录开头，一律跳过
        return tok.endswith(EXT) or tok.startswith(KNOWN_PREFIXES)
    return tok.endswith(BARE_EXT)


def resolves(tok, roots, index):
    tok = tok.replace(os.sep, "/")
    for root in roots:
        if os.path.exists(os.path.normpath(os.path.join(root, tok))):
            return True
    if "/" in tok:
        for prefix in KNOWN_PREFIXES:
            for root in (HOST_ROOT, EDGE_ROOT):
                cand = os.path.normpath(os.path.join(root, prefix.strip("./"), tok))
                if os.path.exists(cand):
                    return True
    base = os.path.basename(tok)
    if base in index and (tok == base or tok.startswith("stl/") or tok.startswith("docs/")):
        return True
    if tok.startswith(("docs/", "stl/", "hardware/", "tools/", "pi/", "pc/", "shared/")):
        return base in index
    return False


def module_exists(mod):
    parts = mod.split(".")
    for root in REPO_ROOTS:
        base = os.path.join(root, *parts)
        if os.path.isfile(base + ".py") or os.path.isfile(os.path.join(base, "__init__.py")):
            return True
        if os.path.isfile(os.path.join(base, "__main__.py")):
            return True
    return False


def make_targets():
    mk = os.path.join(HOST_ROOT, "Makefile")
    if not os.path.isfile(mk):
        return set()
    out = set()
    for line in open(mk, encoding="utf-8"):
        m = re.match(r"^([A-Za-z0-9][\w.-]*):", line)
        if m:
            out.add(m.group(1))
    return out


def check(paths, verbose):
    targets = make_targets()
    index = build_index()
    bad = checked = 0
    for doc in iter_md(paths):
        roots = candidate_roots(doc)
        rel_doc = os.path.relpath(doc, WORKSPACE)
        for lineno, line in enumerate(open(doc, encoding="utf-8").read().splitlines(), 1):
            if "lint:ignore" in line:      # 要写反例/占位命令时用这个标记
                continue
            for mod in MODULE_CMD.findall(line):
                if mod in STDLIB or mod.split(".")[0] in STDLIB:
                    continue
                checked += 1
                if not module_exists(mod):
                    bad += 1
                    print("MODULE  %s:%d  python -m %s" % (rel_doc, lineno, mod))
                elif verbose:
                    print("ok      %s:%d  %s" % (rel_doc, lineno, mod))
            for tgt in MAKE_CMD.findall(line):
                checked += 1
                if targets and tgt not in targets:
                    bad += 1
                    print("TARGET  %s:%d  make %s" % (rel_doc, lineno, tgt))
                elif verbose:
                    print("ok      %s:%d  make %s" % (rel_doc, lineno, tgt))
            for tok in ([] if rel_doc in PATH_SKIP else CODE_SPAN.findall(line)):
                if not looks_like_path(tok) or is_runtime(tok) or tok in ALLOW_EXACT:
                    continue
                checked += 1
                if not resolves(tok, roots, index):
                    bad += 1
                    print("PATH    %s:%d  %s" % (rel_doc, lineno, tok))
                elif verbose:
                    print("ok      %s:%d  %s" % (rel_doc, lineno, tok))
    print("\nchecked %d reference(s), %d problem(s)" % (checked, bad))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验 markdown 里的本地路径/模块/make 目标")
    ap.add_argument("--paths", nargs="*", default=None, help="要检查的 md 文件或目录")
    ap.add_argument("--list", action="store_true", help="打印通过项")
    args = ap.parse_args(argv)
    paths = args.paths or [
        os.path.join(HOST_ROOT, "README.md"),
        os.path.join(HOST_ROOT, "docs"),
        os.path.join(WORKSPACE, "README.md"),
        os.path.join(WORKSPACE, "docs"),
        os.path.join(EDGE_ROOT, "README.md"),
        os.path.join(EDGE_ROOT, "docs"),
        os.path.join(EDGE_ROOT, "hardware"),
    ]
    return check([p for p in paths if os.path.exists(p)], args.list)


if __name__ == "__main__":
    sys.exit(main())
