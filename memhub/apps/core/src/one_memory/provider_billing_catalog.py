"""Only verified provider hosts are eligible for financial credentials."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from one_memory.model_profiles import ModelProfile


@dataclass(frozen=True)
class BillingField:
    name: str
    required: bool = True
    secret: bool = True


@dataclass(frozen=True)
class BillingProvider:
    name: str
    endpoint: str | None
    billing_url: str
    docs_url: str
    fields: tuple[BillingField, ...] = ()
    requires_authorization: bool = False


PROVIDERS = {
    "deepseek": BillingProvider(
        "deepseek",
        "https://api.deepseek.com/user/balance",
        "https://platform.deepseek.com/usage",
        "https://api-docs.deepseek.com/zh-cn/api/get-user-balance/",
    ),
    "moonshot_cn": BillingProvider(
        "moonshot_cn",
        "https://api.moonshot.cn/v1/users/me/balance",
        "https://platform.kimi.com/console/fee-detail",
        "https://platform.kimi.com/docs/api/balance",
    ),
    "moonshot_global": BillingProvider(
        "moonshot_global",
        "https://api.moonshot.ai/v1/users/me/balance",
        "https://platform.kimi.ai/console/fee-detail",
        "https://platform.kimi.ai/docs/api/balance",
    ),
    "siliconflow": BillingProvider(
        "siliconflow",
        "https://api.siliconflow.cn/v1/user/info",
        "https://cloud.siliconflow.cn",
        "https://siliconflow.readme.io/reference/user-info",
    ),
    "openrouter": BillingProvider(
        "openrouter",
        "https://openrouter.ai/api/v1/key",
        "https://openrouter.ai/activity",
        "https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key",
        (BillingField("api_key"),),
    ),
    "openai": BillingProvider(
        "openai",
        "https://api.openai.com/v1/organization/costs",
        "https://platform.openai.com/usage",
        "https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs",
        (
            BillingField("api_key"),
            BillingField("project_id", False, False),
            BillingField("api_key_id", False, False),
        ),
        True,
    ),
    "aliyun": BillingProvider(
        "aliyun",
        "https://business.aliyuncs.com/",
        "https://usercenter2.aliyun.com",
        "https://help.aliyun.com/zh/user-center/developer-reference/api-bssopenapi-2017-12-14-queryaccountbalance",
        (
            BillingField("access_key_id"),
            BillingField("access_key_secret"),
            BillingField("security_token", False),
        ),
        True,
    ),
    "tencent": BillingProvider(
        "tencent",
        "https://billing.tencentcloudapi.com/",
        "https://console.cloud.tencent.com/expense/overview",
        "https://cloud.tencent.com/document/api/555/20253",
        (
            BillingField("secret_id"),
            BillingField("secret_key"),
            BillingField("session_token", False),
        ),
        True,
    ),
    "azure": BillingProvider(
        "azure",
        "https://management.azure.com/",
        "https://portal.azure.com",
        "https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage",
        (BillingField("access_token"), BillingField("resource_id", True, False)),
        True,
    ),
    "minimax": BillingProvider(
        "minimax",
        None,
        "https://platform.minimax.io",
        "https://platform.minimax.io/docs/faq/about-account",
    ),
    "minimax_cn": BillingProvider(
        "minimax_cn",
        None,
        "https://platform.minimaxi.com",
        "https://platform.minimaxi.com/docs/faq/about-account",
    ),
    "zhipu": BillingProvider("zhipu", None, "https://bigmodel.cn", "https://docs.bigmodel.cn"),
    "zai": BillingProvider("zai", None, "https://z.ai/manage-apikey/billing", "https://docs.z.ai"),
}
HOSTS = {
    "api.deepseek.com": "deepseek",
    "api.moonshot.cn": "moonshot_cn",
    "api.moonshot.ai": "moonshot_global",
    "api.siliconflow.cn": "siliconflow",
    "openrouter.ai": "openrouter",
    "api.openai.com": "openai",
    "api.hunyuan.cloud.tencent.com": "tencent",
    "hunyuan.tencentcloudapi.com": "tencent",
    "api.minimax.io": "minimax",
    "api.minimaxi.com": "minimax_cn",
    "api.minimax.chat": "minimax_cn",
    "open.bigmodel.cn": "zhipu",
    "api.z.ai": "zai",
}


def billing_provider(profile: ModelProfile) -> BillingProvider | None:
    try:
        url = urlsplit(profile.base_url or "")
        if (
            profile.provider_kind != "openai_compatible"
            or url.scheme != "https"
            or url.port not in (None, 443)
            or url.username is not None
            or url.password is not None
        ):
            return None
        host = url.hostname or ""
    except ValueError:
        return None
    name = HOSTS.get(host)
    if host.endswith((".openai.azure.com", ".services.ai.azure.com")):
        name = "azure"
    if host.endswith(".aliyuncs.com") and host.split(".")[0] in (
        "dashscope",
        "dashscope-intl",
        "dashscope-us",
    ):
        name = "aliyun"
    return PROVIDERS.get(name) if name else None
