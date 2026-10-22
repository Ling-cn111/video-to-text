"""部署清单（`s.yaml`）与代码的契约（S2-P3）。

为什么要有这组测试：`s.yaml` 是唯一一份「跑在云上」的配置，却完全不被 Python
代码引用——它和 `WORKER_PATH`、`ENQUEUE_MODE`、端口、密钥注入方式之间只有口头
约定。任何一处漂移都只能在真机部署后才发现（而且往往表现为 404 / 401 / 任务
永远 processing），排查成本极高。

这里只断言**会导致线上直接坏掉**的硬契约，不追求覆盖所有字段：

- 两个函数都在、镜像/启动命令对得上
- api 走 fc、worker 走 inline 且 `TASK_INIT_MODE=none`
- `FC_WORKER_PATH` 与 `worker_app.WORKER_PATH` 一致（否则 404）
- 触发器只放行必要方法、认证方式与 `WORKER_TOKEN` 兜底一致
- 异步重试/存活时间在 FC 允许区间内
- 仓库里不含明文密钥（全部 `${env('NAME')}` 占位）

`yaml` 由 `uvicorn[standard]` 传递引入，缺失时跳过而不是失败。
"""
import re
from pathlib import Path

import pytest

from app.worker_app import INVOKE_PATH, WORKER_PATH

yaml = pytest.importorskip("yaml")

MANIFEST = Path(__file__).resolve().parents[2] / "s.yaml"

# `${env('NAME')}` / `${env('NAME', '默认值')}`——注意必须有引号
_ENV_MAGIC = re.compile(r"^\$\{env\('([A-Z0-9_]+)'(?:, '([^']*)')?\)\}$")


@pytest.fixture(scope="module")
def manifest() -> dict:
    if not MANIFEST.exists():
        pytest.skip(f"未找到部署清单：{MANIFEST}")
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def api(manifest) -> dict:
    return manifest["resources"]["api"]["props"]


@pytest.fixture(scope="module")
def worker(manifest) -> dict:
    return manifest["resources"]["worker"]["props"]


def _env(props: dict) -> dict:
    return props["environmentVariables"]


def _http_trigger(props: dict) -> dict:
    triggers = [t for t in props["triggers"] if t["triggerType"] == "http"]
    assert len(triggers) == 1, "每个函数应恰好有一个 HTTP 触发器"
    return triggers[0]


# ---- 结构 ----

def test_manifest_is_fc3_edition_3(manifest):
    assert manifest["edition"] == "3.0.0"
    for name in ("api", "worker"):
        assert manifest["resources"][name]["component"] == "fc3"


def test_both_functions_use_the_same_image(manifest, api, worker):
    """同一镜像、只换启动命令——这是「Worker 无需单独构建」的前提。"""
    api_image = api["customContainerConfig"]["image"]
    worker_image = worker["customContainerConfig"]["image"]
    assert api_image == worker_image
    assert api_image.startswith("${vars."), "镜像地址应由 vars 组装，避免两处硬编码不一致"


@pytest.mark.parametrize(
    ("props_name", "expected_app"),
    [("api", "app.main:app"), ("worker", "app.worker_app:app")],
)
def test_container_commands_launch_expected_asgi_app(manifest, props_name, expected_app):
    props = manifest["resources"][props_name]["props"]
    command = props["customContainerConfig"]["command"]
    assert command[0] == expected_app


@pytest.mark.parametrize("props_name", ["api", "worker"])
def test_listen_port_matches_health_check_and_command(manifest, props_name):
    """端口三处必须一致：容器命令 / port 字段 / 健康检查走同一端口。

    不一致的典型表现是「部署成功但请求 502、健康检查一直失败」。
    """
    container = manifest["resources"][props_name]["props"]["customContainerConfig"]
    command = container["command"]
    port = container["port"]

    assert isinstance(port, int)
    assert command[command.index("--port") + 1] == str(port)
    assert command[command.index("--host") + 1] == "0.0.0.0"
    assert container["healthCheckConfig"]["httpGetUrl"] == "/api/health"


# ---- 分发契约 ----

def test_api_dispatches_to_fc_and_worker_does_not(api, worker):
    """api 负责投递，worker 绝不能再分发（否则会无限套娃）。"""
    assert _env(api)["ENQUEUE_MODE"] == "fc"
    assert _env(worker)["ENQUEUE_MODE"] == "inline"


def test_worker_path_matches_worker_app_constant(api):
    """`FC_WORKER_PATH` 与 worker 实际挂载点不一致 → 异步调用直接 404。"""
    assert _env(api)["FC_WORKER_PATH"] == WORKER_PATH


def test_invoke_alias_is_documented_as_fallback(api):
    """`/invoke` 是 InvokeFunction 的固定落点，不能被误配成主路径。"""
    assert _env(api)["FC_WORKER_PATH"] != INVOKE_PATH


def test_api_worker_token_is_shared(api, worker):
    """两侧 token 必须来自同一个变量，否则 worker 一律 401。"""
    assert _env(api)["WORKER_TOKEN"] == _env(worker)["WORKER_TOKEN"]


def test_api_worker_url_is_placeholder_so_first_deploy_succeeds(api):
    """首轮部署时 worker 地址还不存在：必须能渲染成空串，不能抛错。"""
    match = _ENV_MAGIC.match(_env(api)["FC_WORKER_URL"])
    assert match, "FC_WORKER_URL 必须是 ${env('NAME', '默认值')} 形式"
    assert match.group(2) == ""


# ---- 启动恢复 ----

def test_worker_never_recovers_interrupted_tasks(api, worker):
    """Worker 冷启动频繁，recover 会把其他实例在跑的任务误判为中断。"""
    assert _env(api)["TASK_INIT_MODE"] == "recover"
    assert _env(worker)["TASK_INIT_MODE"] == "none"


def test_worker_lock_ttl_far_exceeds_heartbeat(worker):
    env = _env(worker)
    assert int(env["TASK_LOCK_SECONDS"]) >= 10 * int(env["TASK_HEARTBEAT_SECONDS"])
    assert int(env["TASK_STALE_SECONDS"]) >= 10 * int(env["TASK_HEARTBEAT_SECONDS"])


def test_cloud_deployment_disables_local_asr_fallback(api, worker):
    """镜像不含本地 ASR：降级到 local 只会多报一个「未安装」错误。"""
    for env in (_env(api), _env(worker)):
        assert env["ASR_ENGINE"] == "cloud"
        assert env["ASR_ENGINE_FALLBACK"] == ""


# ---- 触发器 ----

def test_worker_trigger_only_allows_post(worker):
    """worker 只接受投递，不需要暴露读接口。"""
    assert _http_trigger(worker)["triggerConfig"]["methods"] == ["POST"]


def test_api_trigger_allows_browser_methods(api):
    methods = set(_http_trigger(api)["triggerConfig"]["methods"])
    assert {"GET", "POST", "OPTIONS"} <= methods


@pytest.mark.parametrize("props_name", ["api", "worker"])
def test_triggers_are_anonymous_with_app_level_auth(manifest, props_name):
    """走 anonymous + 应用层校验，是为了避免引入 ACS3 签名与 AK/SK。"""
    trigger = _http_trigger(manifest["resources"][props_name]["props"])
    assert trigger["triggerConfig"]["authType"] == "anonymous"
    assert trigger.get("qualifier") == "LATEST"


# ---- 异步调用配置 ----

def test_async_invoke_config_within_fc_limits(worker):
    """FC 允许区间：重试 [0,8]、事件存活 [1,604800]。"""
    config = worker["asyncInvokeConfig"]
    assert 0 <= config["maxAsyncRetryAttempts"] <= 8
    assert 1 <= config["maxAsyncEventAgeInSeconds"] <= 604800


def test_async_event_age_matches_task_ttl(api, worker):
    """事件存活期不该超过任务 TTL：过期事件即使重放也读不到任务。"""
    assert int(_env(worker)["TASK_TTL_SECONDS"]) >= worker["asyncInvokeConfig"][
        "maxAsyncEventAgeInSeconds"
    ]


# ---- 密钥与规格 ----

def test_no_plaintext_secrets_in_manifest(manifest):
    """所有形似密钥的值都必须是 `${env(...)}` 占位，不能是明文。"""
    secret_keys = {
        "REDIS_URL",
        "CLOUD_ASR_API_KEY",
        "DEEPSEEK_API_KEY",
        "WORKER_TOKEN",
        "FC_WORKER_URL",
        "CORS_ORIGINS",
        "DASHSCOPE_API_KEY",
    }
    for props in (r["props"] for r in manifest["resources"].values()):
        for key, value in _env(props).items():
            if key in secret_keys:
                assert _ENV_MAGIC.match(str(value)), f"{key} 必须是 ${{env('...')}} 占位，实际为 {value!r}"


def test_env_magic_names_are_quoted(manifest):
    """`${env(NAME)}`（无引号）会被当成 JS 变量，`s deploy` 直接失败。

    只看非注释行：文件头的注释里就写着反例 `${env(NAME)}`。
    """
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for match in re.finditer(r"\$\{env\(([^)]*)\)\}", line):
            assert match.group(1).lstrip().startswith("'"), f"未加引号的 env 魔法变量：{match.group(0)}"


def test_worker_is_sized_for_oom_not_cost(worker):
    """100MB 音频 + ffmpeg 全程在内存，规格不能抠。"""
    assert worker["memorySize"] >= 4096
    assert worker["cpu"] >= 2
    assert worker["timeout"] >= 600
    assert worker["instanceConcurrency"] == 1
