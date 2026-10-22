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
- **网络与角色（S2-Deploy）**：两函数都有完整 `vpcConfig`（否则连不上 VPC 内网 Redis）、
  同一套网络、`internetAccess` 保持开启、`role` 默认留空走服务关联角色
- **文档模板与 `s.yaml` 一致**（按 YAML 解析比对 `docs/serverless-deploy.md` §5.1 的模板块）

`yaml` 由 `uvicorn[standard]` 传递引入，缺失时跳过而不是失败。
"""
import re
from pathlib import Path

import pytest

from app.worker_app import INVOKE_PATH, REAP_PATH, WORKER_PATH

yaml = pytest.importorskip("yaml")

MANIFEST = Path(__file__).resolve().parents[2] / "s.yaml"
DOC = Path(__file__).resolve().parents[2] / "docs" / "serverless-deploy.md"

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


def _timer_trigger(props: dict) -> dict:
    triggers = [t for t in props["triggers"] if t["triggerType"] == "timer"]
    assert len(triggers) == 1, "worker 应恰好有一个定时触发器（S2-P4 reaper）"
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
    """`/invoke` 是 InvokeFunction 与事件源触发器的固定落点，不能被误配成主路径。"""
    assert _env(api)["FC_WORKER_PATH"] != INVOKE_PATH
    assert _env(api)["FC_WORKER_PATH"] != REAP_PATH


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


# ---- 定时触发器（S2-P4 reaper）----

def test_worker_has_timer_trigger_for_reaper(worker):
    """僵尸任务清理靠定时触发器驱动；漏配 = 任务永远卡 processing。"""
    trigger = _timer_trigger(worker)
    assert trigger["triggerConfig"]["enable"] is True


def test_api_has_no_timer_trigger(api):
    """reaper 只在 worker 侧跑：API 函数多实例，挂定时器会各自抢锁空转。"""
    assert [t for t in api["triggers"] if t["triggerType"] == "timer"] == []


def test_timer_cron_is_six_field_and_runs_every_five_minutes(worker):
    """FC cron 是 6 段（秒 分 时 日 月 周），最小粒度 1 分钟。

    写成 5 段（Linux cron 习惯）会被 FC 拒绝；间隔必须远小于
    TASK_STALE_SECONDS，否则僵尸任务要等很久才被收割。
    """
    cron = _timer_trigger(worker)["triggerConfig"]["cronExpression"]
    fields = cron.split()
    assert len(fields) == 6, f"FC cron 必须是 6 段，实际：{cron!r}"
    assert fields[1] == "*/5", f"应为每 5 分钟，实际：{cron!r}"


def test_timer_interval_much_smaller_than_stale_threshold(worker):
    """定时周期必须远小于陈旧阈值：否则僵尸任务要等好几轮才被收割。

    从 cron 的「分」字段推出周期（只支持 `*/N` 与固定值两种写法），
    与 TASK_STALE_SECONDS 比对，避免这条断言变成恒真。
    """
    minute_field = _timer_trigger(worker)["triggerConfig"]["cronExpression"].split()[1]
    if minute_field.startswith("*/"):
        interval_seconds = int(minute_field[2:]) * 60
    else:
        interval_seconds = 3600  # 每小时整点触发
    stale = int(_env(worker)["TASK_STALE_SECONDS"])

    assert interval_seconds * 2 <= stale, (
        f"定时周期 {interval_seconds}s 相对陈旧阈值 {stale}s 太长："
        "僵尸任务滞留时间会被放大到「阈值 + 周期」"
    )


def test_worker_reaper_lock_ttl_covers_single_scan(worker):
    """互斥锁 TTL 只需覆盖单次扫描，不能照抄 TASK_LOCK_SECONDS（那是执行锁）。"""
    env = _env(worker)
    assert 0 < int(env["REAPER_LOCK_SECONDS"]) <= 600


def test_timer_payload_is_present_but_not_load_bearing(worker):
    """payload 只用于日志辨识；分流靠 triggerTime/triggerName 特征（见 worker_app）。"""
    payload = _timer_trigger(worker)["triggerConfig"]["payload"]
    assert isinstance(payload, str) and payload


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


# ---- 网络与角色（S2-Deploy）----
#
# 这组断言防的是「部署成功但连不上 Redis」：FC 不会在部署时校验 VPC 连通性，
# 只会在函数执行时报超时，而应用侧又会静默降级 memory——表现成「多实例任务互不可见」，
# 从日志几乎看不出是配置问题。

@pytest.mark.parametrize("props_name", ["api", "worker"])
def test_each_function_has_complete_vpc_config(manifest, props_name):
    """fc3 schema：vpcConfig 结构体里三个字段都是必填，少一个直接部署失败。"""
    vpc = manifest["resources"][props_name]["props"]["vpcConfig"]

    assert isinstance(vpc, dict), "必须是结构体，不能是 auto（auto 建出的 VPC 里没有你的 Redis）"
    assert set(vpc) == {"vpcId", "vSwitchIds", "securityGroupId"}
    for key in ("vpcId", "securityGroupId"):
        assert isinstance(vpc[key], str) and vpc[key]


@pytest.mark.parametrize("props_name", ["api", "worker"])
def test_vswitch_ids_is_a_list(manifest, props_name):
    """schema 允许 array 或字符串 "auto"，但绝不接受单个裸字符串 ID。

    写成 `vSwitchIds: vsw-xxx`（漏了 `-`）会被当成 auto 之外的非法类型，
    报错信息还不直观。
    """
    vswitch_ids = manifest["resources"][props_name]["props"]["vpcConfig"]["vSwitchIds"]

    assert isinstance(vswitch_ids, list) and vswitch_ids
    assert all(isinstance(v, str) and v for v in vswitch_ids)


def test_both_functions_share_the_same_network(api, worker):
    """任务状态写在 Redis：api 与 worker 必须落在同一 VPC/交换机/安全组。

    不一致的后果是单边可用——api 能写、worker 读不到（或反之），
    任务停在 processing 直到 reaper 判超时。
    """
    assert api["vpcConfig"] == worker["vpcConfig"]


@pytest.mark.parametrize("props_name", ["api", "worker"])
def test_vpc_ids_are_env_placeholders(manifest, props_name):
    """仓库不能含账号内的资源 ID（VPC/交换机/安全组都是账号私有信息）。"""
    vpc = manifest["resources"][props_name]["props"]["vpcConfig"]

    for key in ("vpcId", "securityGroupId"):
        assert _ENV_MAGIC.match(vpc[key]), f"{key} 必须是 ${{env('...')}} 占位，实际为 {vpc[key]!r}"
    for value in vpc["vSwitchIds"]:
        assert _ENV_MAGIC.match(value), f"vSwitchIds 项必须是 ${{env('...')}} 占位，实际为 {value!r}"


def test_vpc_config_does_not_silently_fall_back_to_empty():
    """`${env('FC_VPC_ID')}` 必须没有默认值。

    带默认值（如 ''）会让缺配置时「部署成功、运行期连不上 Redis」；
    不带默认值时 `s deploy` 直接报 FC_VPC_ID is not defined，失败得早且清晰。
    """
    text = MANIFEST.read_text(encoding="utf-8")
    for name in ("FC_VPC_ID", "FC_VSWITCH_ID", "FC_SECURITY_GROUP_ID"):
        assert re.search(rf"\$\{{env\('{name}'\)\}}", text), f"{name} 不应带默认值"
        assert not re.search(rf"\$\{{env\('{name}',", text), f"{name} 不应带默认值"


@pytest.mark.parametrize("props_name", ["api", "worker"])
def test_internet_access_stays_on_with_vpc(manifest, props_name):
    """配了 VPC 不等于放弃公网：FC 支持「访问 VPC + 访问公网」并存。

    关掉 internetAccess 后 bilibili 下载、云端 ASR、DeepSeek 全部失败，
    而 Redis 反而正常——排查方向会被完全带偏。
    """
    props = manifest["resources"][props_name]["props"]
    assert props["vpcConfig"], "前提：该函数已配置 VPC"
    assert props["internetAccess"] is True


@pytest.mark.parametrize("props_name", ["api", "worker"])
def test_function_role_is_optional_env_placeholder(manifest, props_name):
    """`role` 留空即用 FC 服务关联角色（已覆盖 SLS 日志 + VPC ENI + 同账号 ACR 拉取）。

    只有函数代码要访问其他云服务时才需要自定义角色，因此默认值必须是空串，
    否则首次部署会被迫先去 RAM 建角色。注意 ARN 格式必须是
    `acs:ram::<账号ID>:role/<角色名>`——只写角色名 fc3 会自动补全，但手写 YAML
    时写错格式会被 FC 拒绝。
    """
    role = manifest["resources"][props_name]["props"]["role"]

    match = _ENV_MAGIC.match(role)
    assert match, f"role 必须是 ${{env('NAME', '默认值')}} 形式，实际为 {role!r}"
    assert match.group(1) == "FC_ROLE_ARN"
    assert match.group(2) == "", "默认值必须为空串（= 使用服务关联角色）"


def test_documented_template_matches_the_real_manifest(manifest):
    """文档里的完整模板必须与仓库 `s.yaml` 一致。

    部署指南是「照着抄就能跑」的东西，一旦与 s.yaml 漂移，读者会抄到旧配置
    （例如漏掉 vpcConfig），而这类问题只会在云上暴露。用测试把两份绑在一起。
    """
    doc = DOC.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- BEGIN:s.yaml -->\s*```yaml\n(.*?)```\s*<!-- END:s.yaml -->",
        doc,
        re.S,
    )
    assert match, "docs/serverless-deploy.md 里找不到 <!-- BEGIN:s.yaml --> 包裹的模板块"

    documented = yaml.safe_load(match.group(1))
    assert documented == manifest, "文档模板与 s.yaml 不一致：请同步 §5 的完整模板"
