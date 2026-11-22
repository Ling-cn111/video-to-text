"""M3 上传功能的一键资源准备（部署机用，替代 docs/serverless-deploy.md §4.4 的控制台操作）。

幂等可重跑：bucket / CORS / 生命周期已存在时跳过或覆盖；RAM 用户与策略已存在时跳过；
AK 只在用户一个都没有时新建（已存在的 AK 秘钥无法二次读取，此时按提示处理）。

凭证来源：Serverless Devs 的默认凭证（~/.s/access.yaml），**不经过命令行参数、不打印明文**。
产出：把 OSS_* 环境变量块追加到仓库根的 deploy.ps1（该文件不入库），随后删除中间产物。

用法：
    cd backend && .venv/Scripts/python.exe -m scripts.setup_oss
"""
import json
import sys
from pathlib import Path

import yaml

REGION = "cn-hangzhou"
OSS_PUBLIC_ENDPOINT = f"oss-{REGION}.aliyuncs.com"
RAM_USER_NAME = "video-to-text-oss-upload"
RAM_POLICY_NAME = "vtt-oss-uploads-minimal"
UPLOAD_PREFIX = "uploads/"
# 重点帧产物前缀（PR #89）：7 天过期——与任务 TTL（TASK_TTL_SECONDS 默认 7 天）
# 对齐，避免「任务还在、帧图先没了」；任务删除后孤儿帧也由同一条规则兜底清理。
FRAMES_PREFIX = "frames/"
FRAMES_EXPIRATION_DAYS = 7


def _decrypt(ciphertext: str, password: str = "SecretKey123") -> str:
    """等价解密 s CLI 的凭证存储：CryptoJS AES-256-CBC（OpenSSL Salted + EVP_BytesToKey/MD5）。

    口令来自 s CLI 源码中的 AES.encrypt 调用（本机安装版本）；仅在本机内存中使用，
    不落盘、不打印。
    """
    import base64
    import hashlib

    from Crypto.Cipher import AES

    data = base64.b64decode(ciphertext)
    if data[:8] != b"Salted__":
        return ciphertext  # 未加密的旧格式原样返回
    salt, body = data[8:16], data[16:]
    derived = b""
    prev = b""
    while len(derived) < 48:  # 32 字节 key + 16 字节 IV
        prev = hashlib.md5(prev + password.encode() + salt).digest()
        derived += prev
    cipher = AES.new(derived[:32], AES.MODE_CBC, derived[32:48])
    padded = cipher.decrypt(body)
    return padded[: -padded[-1]].decode("utf-8")


def load_all_profiles() -> dict[str, dict]:
    """读 Serverless Devs 全部 profile 并解密（不经参数、不回显）。"""
    path = Path.home() / ".s" / "access.yaml"
    if not path.exists():
        sys.exit(f"未找到 Serverless Devs 凭证：{path}（先 s config add）")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    profiles: dict[str, dict] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict) or not entry.get("AccessKeyID"):
            continue
        profiles[name] = {
            "AccessKeyID": _decrypt(str(entry["AccessKeyID"])),
            "AccessKeySecret": _decrypt(str(entry["AccessKeySecret"])),
        }
    if not profiles:
        sys.exit("凭证文件里没有任何可用的 profile")
    return profiles


def pick_working_credential(profiles: dict[str, dict]) -> tuple[str, dict]:
    """依次验证各 profile（STS GetCallerIdentity），返回第一个可用的（别名, 凭证）。"""
    from alibabacloud_sts20150401.client import Client as StsClient
    from alibabacloud_sts20150401 import models as sts_models
    from alibabacloud_tea_openapi import models as openapi_models

    errors: list[str] = []
    for name, cred in profiles.items():
        try:
            config = openapi_models.Config(
                access_key_id=cred["AccessKeyID"],
                access_key_secret=cred["AccessKeySecret"],
                endpoint="sts.aliyuncs.com",
            )
            response = StsClient(config).get_caller_identity()
            account_id = str(response.body.account_id)
            if not account_id.isdigit():
                raise RuntimeError(f"账号 ID 异常（长度 {len(account_id)}）")
            cred["AccountID"] = account_id
            print(f"使用凭证 profile：{name}（验证通过）")
            return name, cred
        except Exception as exc:
            message = str(exc).split("\n")[0][:120]
            errors.append(f"{name}: {message}")
    sys.exit("没有任何可用凭证：\n  " + "\n  ".join(errors))


def ensure_bucket(account_id: str, cred: dict) -> str:
    """创建私有 bucket（不存在时），配 CORS 与 1 天生命周期。返回 bucket 名。"""
    import oss2

    bucket_name = f"vtt-uploads-{account_id}"  # 全局唯一：拼 12 位账号 ID
    auth = oss2.Auth(cred["AccessKeyID"], cred["AccessKeySecret"])
    oss_service = oss2.Service(auth, OSS_PUBLIC_ENDPOINT)

    bucket = oss2.Bucket(auth, OSS_PUBLIC_ENDPOINT, bucket_name)
    try:
        bucket.get_bucket_info()
        print(f"bucket 已存在：{bucket_name}")
    except oss2.exceptions.NoSuchBucket:
        bucket.create_bucket(oss2.models.BUCKET_ACL_PRIVATE)
        print(f"已创建私有 bucket：{bucket_name}")

    bucket = oss2.Bucket(auth, OSS_PUBLIC_ENDPOINT, bucket_name)

    # CORS：浏览器直传 PUT + 代码路径里可能用到 HEAD；预签名 URL 不带 Cookie，放宽来源无凭证面
    bucket.put_bucket_cors(
        oss2.models.BucketCors(
            [
                oss2.models.CorsRule(
                    allowed_origins=["*"],
                    allowed_methods=["PUT", "HEAD"],
                    allowed_headers=["*"],
                    expose_headers=["ETag"],
                    max_age_seconds=7200,
                )
            ]
        )
    )
    print("CORS 已配置（PUT/HEAD，来源 *，暴露 ETag）")

    # 生命周期（put_bucket_lifecycle 是**整体替换**语义：两条规则必须在同一次
    # 调用里提交，单独再 put 一次会把第一条覆盖掉）：
    # - uploads/ 前缀 1 天删除——上传对象堆积的唯一兜底（成功即删 + 失败留 1 天重试都靠它收尾）
    # - frames/ 前缀 7 天删除——重点帧产物（与任务 TTL 对齐，见常量处注释）
    rule_uploads = oss2.models.LifecycleRule(
        "vtt-uploads-expire",
        UPLOAD_PREFIX.rstrip("/"),
        expiration=oss2.models.LifecycleExpiration(days=1),
    )
    rule_frames = oss2.models.LifecycleRule(
        "vtt-frames-expire",
        FRAMES_PREFIX.rstrip("/"),
        expiration=oss2.models.LifecycleExpiration(days=FRAMES_EXPIRATION_DAYS),
    )
    bucket.put_bucket_lifecycle(oss2.models.BucketLifecycle([rule_uploads, rule_frames]))
    print(
        "生命周期已配置（uploads/ 1 天删除；frames/ "
        f"{FRAMES_EXPIRATION_DAYS} 天删除）"
    )
    return bucket_name


def ensure_ram_user_and_ak(bucket_name: str, cred: dict) -> tuple[str, str | None]:
    """创建最小权限 RAM 用户与 AK。返回 (AK ID, AK Secret 或 None=已有 AK 需人工处理)。"""
    from alibabacloud_ram20150501.client import Client as RamClient
    from alibabacloud_ram20150501 import models as ram_models
    from alibabacloud_tea_openapi import models as openapi_models

    config = openapi_models.Config(
        access_key_id=cred["AccessKeyID"],
        access_key_secret=cred["AccessKeySecret"],
        # RAM 是全局服务：endpoint 没有地域子域（ram.cn-hangzhou.aliyuncs.com 不存在）
        endpoint="ram.aliyuncs.com",
    )
    ram = RamClient(config)

    # 用户
    try:
        ram.get_user(ram_models.GetUserRequest(user_name=RAM_USER_NAME))
        print(f"RAM 用户已存在：{RAM_USER_NAME}")
    except Exception:
        ram.create_user(ram_models.CreateUserRequest(user_name=RAM_USER_NAME))
        print(f"已创建 RAM 用户：{RAM_USER_NAME}")

    # 最小权限策略：uploads/（上传中转）+ frames/（重点帧产物）。
    # frames/ 需要 PutObject（Worker 上传帧图）与 GetObject（前端经预签名 GET
    # 拉图——签名授权底层校验的是 AK 对应主体的 oss:GetObject 权限）。
    policy_document = {
        "Version": "1",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["oss:PutObject", "oss:GetObject", "oss:HeadObject", "oss:DeleteObject"],
                "Resource": f"acs:oss:*:*:{bucket_name}/{UPLOAD_PREFIX}*",
            },
            {
                "Effect": "Allow",
                "Action": ["oss:PutObject", "oss:GetObject"],
                "Resource": f"acs:oss:*:*:{bucket_name}/{FRAMES_PREFIX}*",
            },
        ],
    }
    try:
        ram.get_policy(ram_models.GetPolicyRequest(policy_name=RAM_POLICY_NAME))
        print(f"RAM 策略已存在：{RAM_POLICY_NAME}")
    except Exception:
        ram.create_policy(
            ram_models.CreatePolicyRequest(
                policy_name=RAM_POLICY_NAME,
                policy_document=json.dumps(policy_document),
            )
        )
        print(f"已创建 RAM 策略：{RAM_POLICY_NAME}")
    ram.attach_policy_to_user(
        ram_models.AttachPolicyToUserRequest(
            policy_type="Custom", policy_name=RAM_POLICY_NAME, user_name=RAM_USER_NAME
        )
    )
    print("策略已绑定到用户")

    # AK：已存在的 AK 秘钥无法二次读取——只有「一个 AK 都没有」时才创建
    listed = ram.list_access_keys(ram_models.ListAccessKeysRequest(user_name=RAM_USER_NAME))
    keys = listed.body.access_keys.access_key or []
    if keys:
        print(f"用户已有 {len(keys)} 个 AK，跳过创建（秘钥无法二次读取；沿用 deploy.ps1 里的值，或在控制台删除后重跑）")
        return keys[0].access_key_id, None

    created = ram.create_access_key(ram_models.CreateAccessKeyRequest(user_name=RAM_USER_NAME))
    ak = created.body.access_key
    print("已创建新的 AccessKey")
    return ak.access_key_id, ak.access_key_secret


def append_deploy_block(bucket_name: str, ak_id: str, ak_secret: str | None) -> None:
    """把 OSS_* 变量块追加到 deploy.ps1（不入库）。秘钥只写文件、不打印。"""
    repo_root = Path(__file__).resolve().parents[2]
    deploy_ps1 = repo_root / "deploy.ps1"
    if "OSS_BUCKET" in deploy_ps1.read_text(encoding="utf-8"):
        print("deploy.ps1 已含 OSS_* 块，跳过追加（如需更新请手工改）")
        return
    if ak_secret is None:
        sys.exit("deploy.ps1 未配置 OSS_* 且无法自动生成新 AK——请按上方提示处理后重跑")

    block = (
        "\n\n# ---- 本地文件上传（M3）：由 scripts/setup_oss.py 生成 ----\n"
        "# 注意：若上面 AK 与部署凭证相同（回退路径），其权限面大于「最小权限专用用户」；\n"
        "# 建议之后在主账号手动建只限本 bucket uploads/* 的 RAM 用户并替换（见 docs/serverless-deploy.md §4.4）\n"
        f'$env:OSS_BUCKET              = "{bucket_name}"\n'
        f'$env:OSS_ENDPOINT            = "https://{OSS_PUBLIC_ENDPOINT}"\n'
        f'$env:OSS_INTERNAL_ENDPOINT   = "https://oss-{REGION}-internal.aliyuncs.com"\n'
        f'$env:OSS_ACCESS_KEY_ID       = "{ak_id}"\n'
        f'$env:OSS_ACCESS_KEY_SECRET   = "{ak_secret}"\n'
    )
    with open(deploy_ps1, "a", encoding="utf-8") as handle:
        handle.write(block)
    print(f"OSS_* 变量块已追加到 {deploy_ps1}（文件不入库）")


if __name__ == "__main__":
    profiles = load_all_profiles()
    alias, cred = pick_working_credential(profiles)
    bucket = ensure_bucket(cred["AccountID"], cred)
    try:
        ak_id, ak_secret = ensure_ram_user_and_ak(bucket, cred)
    except Exception as exc:
        # 常见原因：部署凭证本身是 RAM 子用户、没有 ram:CreateUser 权限。
        # 回退：直接用当前部署凭证作为 OSS_*（功能可用，但权限面大于最小权限；
        # deploy.ps1 注释会标注，后续可由主账号建专用用户替换）。
        message = str(exc).split("\n")[0][:160]
        print(f"\nRAM 建用户失败（{message}）")
        print("回退：沿用当前部署凭证作为 OSS_*（权限面较大，建议之后在主账号里换成专用最小权限用户）")
        ak_id, ak_secret = cred["AccessKeyID"], cred["AccessKeySecret"]
    append_deploy_block(bucket, ak_id, ak_secret)
    if alias != "default":
        print(f"\n提醒：s.yaml 绑定 access=default，而当前可用凭证在 {alias}；"
              "部署前请运行 `s config add -f -a default` 把同一对 key 覆盖到 default。")
    print("\n完成。下一步：ACR 控制台对 main 构建新镜像（tag v1），然后 .\\deploy.ps1 -NoDeploy 预检。")
