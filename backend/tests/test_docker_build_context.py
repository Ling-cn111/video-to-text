"""Docker 构建上下文（`backend/.dockerignore` + `Dockerfile`）的契约。

为什么单独测这一层：本地 `docker build` 和 ACR 控制台「代码源构建」用的都是
真实 Docker 语义，一旦 `.dockerignore` 把 `Dockerfile` 里 `COPY` 的路径排除掉，
构建会在很靠后的步骤报：

    COPY failed: no source files were specified

这个错误不会告诉你「是被 .dockerignore 排除了」，只会让你怀疑路径写错、
文件名拼错，非常难查。这里用测试把它提前拦住。

另外钉住一条容易漏的语法：`.dockerignore` 里的通配符**只对根目录生效**。
写 `*.pyc` 匹配不到 `app/__pycache__/x.pyc`，必须写 `**/*.pyc`。
（实测确认：旧写法把 26 个 .pyc 全放进了构建上下文。）
"""
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
DOCKERFILE = BACKEND / "Dockerfile"
DOCKERIGNORE = BACKEND / ".dockerignore"


def _ignore_rules() -> list[str]:
    """`.dockerignore` 里的有效规则（去掉空行与注释）。"""
    if not DOCKERIGNORE.is_file():
        return []
    return [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _copy_sources() -> list[str]:
    """Dockerfile 里所有 `COPY`/`ADD` 的**源**路径（排除 `--from=` 跨阶段拷贝）。

    只保留相对构建上下文的路径 —— 这些才是 .dockerignore 会影响到的。
    """
    sources: list[str] = []
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"(?:COPY|ADD)\s+(.+)$", line, re.IGNORECASE)
        if not match:
            continue

        parts = match.group(1).split()
        # 去掉 --chown=... / --chmod=... 之类的 flag
        parts = [p for p in parts if not p.startswith("--")]
        # COPY --from=builder ... 的源在别的阶段里，与上下文无关
        if re.search(r"--from=", match.group(1)):
            continue
        if len(parts) < 2:
            continue

        sources.extend(parts[:-1])  # 最后一个是目标路径

    return sources


def _glob_to_regex(pattern: str) -> str:
    """把 .dockerignore 的 glob 片段翻译成正则（只覆盖 Docker 用到的语法）。

    支持：`*`（不跨目录）、`?`、`[abc]`、`**/`（任意层级）。
    """
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue

        ch = pattern[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(r"\[")
            else:
                out.append("[" + pattern[i + 1 : j] + "]")
                i = j + 1
                continue
        else:
            out.append(re.escape(ch))
        i += 1

    return "".join(out)


def _rule_matches_path(rule: str, path: str) -> bool:
    """判断一条 .dockerignore 规则是否会排除给定路径。

    语义按 Docker：规则匹配某个**目录**时，其下所有内容一并排除。
    """
    path = path[2:] if path.startswith("./") else path
    path = path.strip("/")
    rule = rule.rstrip("/")
    if not rule or not path:
        return False

    regex = "^" + _glob_to_regex(rule) + "$"

    if re.match(regex, path):
        return True

    # 目录规则：逐级检查祖先目录是否被匹配
    parts = path.split("/")
    return any(re.match(regex, "/".join(parts[:n])) for n in range(1, len(parts)))


def test_dockerfile_exists():
    assert DOCKERFILE.is_file(), f"缺少 {DOCKERFILE}"


def test_dockerignore_does_not_exclude_dockerfile():
    """`.dockerignore` 不得排除 Dockerfile 本身。

    Docker 自己总能读到 Dockerfile，所以本地构建不会因此失败——正因为「不会
    立刻炸」，这条才容易被人当成无害的优化加进来，然后在别的构建工具里暴雷。
    """
    offenders = [r for r in _ignore_rules() if r.rstrip("/") in ("Dockerfile", "*", "**", "**/*")]
    assert not offenders, f".dockerignore 排除了 Dockerfile：{offenders}"


@pytest.mark.parametrize("source", _copy_sources())
def test_copy_source_exists_in_build_context(source):
    """Dockerfile 里 COPY 的每个源都必须真实存在于构建上下文（backend/）。"""
    target = BACKEND / source
    assert target.exists(), (
        f"Dockerfile 引用了 {source!r}，但 {target} 不存在；"
        "构建会报 COPY failed"
    )


@pytest.mark.parametrize("source", _copy_sources())
def test_copy_source_is_not_dockerignored(source):
    """Dockerfile 里 COPY 的源不能被 .dockerignore 排除。

    这是真实会踩的坑：`backend/.dockerignore` 早先写过 `Dockerfile` 与
    `scripts/`，一旦有人顺手把 `app/` 或 `requirements*.txt` 也加进去，
    构建就找不到文件，而报错完全指不到 .dockerignore。
    """
    offenders = [r for r in _ignore_rules() if _rule_matches_path(r, source)]
    assert not offenders, (
        f".dockerignore 的 {offenders} 会排除 Dockerfile 的 COPY 源 {source!r}，"
        "构建会报 COPY failed: no source files were specified"
    )


def test_recursive_wildcards_use_double_star_prefix():
    """`.dockerignore` 的 `*` 通配符不跨目录，扩展名类规则要带 `**/`。

    `*.pyc` 放过 `app/__pycache__/x.pyc`；`*.md` 放过 `app/README.md`。
    这类规则看着对、实际漏，症状是构建上下文莫名其妙变大，很难联想到写法。

    以 `.` 开头的规则（`.env.*.local`、`.audio*/`）不算：它们本来就只针对
    仓库根目录下的固定文件/目录，这是有意的。
    """
    bad = [
        r
        for r in _ignore_rules()
        if r.startswith("*") and not r.startswith("**/") and "/" not in r
    ]
    assert not bad, (
        f"这些规则只对根目录生效，应改成 **/ 前缀：{bad}；"
        "例如 *.pyc → **/*.pyc"
    )


def test_env_local_is_excluded():
    """含真实密钥的 .env.local 绝不能被塞进镜像。"""
    rules = _ignore_rules()
    assert any(_rule_matches_path(r, ".env.local") for r in rules), (
        ".dockerignore 必须排除 .env.local（含真实 Key），"
        f"当前规则：{rules}"
    )
