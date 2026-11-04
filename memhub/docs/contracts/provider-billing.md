# Provider financial queries

Financial reads are independent of generation usage and local token-price
estimates. Provider responses retain account/key/organization/resource scope;
unknown fields are null, not zero. Currencies are never converted or combined.

| Provider | Implemented official query | Credential and scope |
| --- | --- | --- |
| DeepSeek | `GET /user/balance` | Existing model key; account balance |
| Kimi / Moonshot CN and international | `GET /v1/users/me/balance` | Existing regional model key; CN: CNY; international: USD; available, cash and voucher breakdown |
| SiliconFlow CN | `GET /v1/user/info` | Existing model key; total and charge balance, no guessed gift breakdown |
| OpenRouter | `GET /api/v1/key` | Existing model key; effective key quota plus provider day/week/month/lifetime USD usage, not account balance |
| OpenRouter account credits | `GET /api/v1/credits` | Separate Management Key; purchased minus used credits, not daily spend |
| OpenAI | `GET /v1/organization/costs` | Separate Admin key; month-to-date and day costs, optional project/key-ID filters |
| Alibaba Cloud / Bailian | `QueryAccountBalance` RPC | Separate AccessKey ID/secret and optional STS token; account available/cash amount |
| Tencent Cloud / Hunyuan | `DescribeAccountBalance` | Separate SecretId/key and optional session token; real balance/cash/grants, cents converted to CNY |
| Azure OpenAI | Cost Management Query | Separate valid management access token with resource-group cost-read access; costs filtered to the resource matching the model endpoint, not an account balance |
| MiniMax and Zhipu / Z.ai | Official console link | Automatic balance adapter not verified/implemented; no undocumented console calls |
| Other proxies/providers | User's billing link and local usage | Model name does not identify the billing provider |

Configure billing authorization in the financial card. Credentials go to the OS
secret store; `billing-authorizations.json` contains references only. They are
never returned to the renderer, never replace generation credentials, and are
removed when a model profile is deleted. Balance cache identity includes the
billing credential reference so rotation/removal invalidates prior snapshots.
No permissions, accounts or keys are created by this feature.

Only catalogued HTTPS hosts/ports and fixed official financial endpoints are
eligible. Redirects are disabled. Alibaba RPC signed parameters go in a POST
body, keeping STS tokens out of URL logs. The Tencent implementation is tested
against an independently generated OpenSSL TC3 vector. OpenAI pagination is
consumed with a cycle/page bound; an incomplete Azure page is rejected instead
of claiming a full cost. An Azure resource ID must match the configured endpoint.

Successful reads are cached for 60s, errors for 10s; manual refresh has a 5s
minimum interval. Missing credentials, required billing authorization, unsupported
adapters and transient failures are distinct. None of them appears as a zero
balance. Official costs can be delayed and may cover other apps using the same
account/key/resource; local memory costs remain current-price estimates.

## Official sources

- [DeepSeek balance](https://api-docs.deepseek.com/zh-cn/api/get-user-balance/)
- [Kimi CN](https://platform.kimi.com/docs/api/balance), [Kimi international](https://platform.kimi.ai/docs/api/balance)
- [SiliconFlow user information](https://siliconflow.readme.io/reference/user-info)
- [OpenRouter current key](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key), [credits](https://openrouter.ai/docs/api/api-reference/credits/get-credits)
- [OpenAI costs](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs)
- [Alibaba balance](https://help.aliyun.com/zh/user-center/developer-reference/api-bssopenapi-2017-12-14-queryaccountbalance), [RPC signatures](https://help.aliyun.com/en/cmn/developer-reference/signature-mechanism)
- [Tencent balance](https://cloud.tencent.com/document/api/555/20253), [TC3 signatures](https://intl.cloud.tencent.com/document/api/1284/75535)
- [Azure cost queries](https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage)
- [MiniMax account help](https://platform.minimax.io/docs/faq/about-account)

Adapters other than DeepSeek have protocol/contract tests but have not been
verified with the user's live provider credentials. Cloud billing remains in
"authorization required" until appropriate independent credentials are supplied.
