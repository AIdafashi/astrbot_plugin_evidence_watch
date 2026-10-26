# 网页变化证据哨兵 / Evidence Watch

AstrBot + [changedetection.io](https://github.com/dgtlmoon/changedetection.io)。一句话建立网页监测，只有变化符合条件才通知，附来源、快照时间、原文证据和增删差异。

## Setup
需要独立运行的 changedetection.io 实例和 API Key、AstrBot >=4.29.3，以及当前会话配置的模型。插件**不会安装或代管**监测服务。通过仓库 URL 安装插件，配置实例根地址（例如 `http://localhost:5000`，不含 `/api/v1`）和 API Key。

1. `/watch whoami` 获取当前会话ID，管理员把它加入 `allowed_sessions`。
2. 管理员在 `allowed_domains` 写允许监测的精确域名。不接受通配符和子域名自动继承；建议只开放受信任的公开站点。changedetection.io 自身的出站访问策略仍由实例管理员管理。
3. `/watch add https://example.com/changelog "新增视频编辑功能时提醒我"`
4. `/watch list` 查看自己创建的监测；`/watch check` 立即检查服务已有的快照；`/watch stop UUID` 暂停自己的监测并保留历史。
也可直接对助手说“盯住这个更新页，新增视频编辑功能时通知我”，由 `watch_page_with_evidence` 工具创建。

## Behavior
- 首份快照建立基线，不误报为新增；按快照顺序补查离线期间的变化。
- 仅发生变化时调用当前会话模型。模型必须返回严格 JSON，命中结论必须引用真实变化里的文字；页面内容作为不可信数据处理。
- SQLite保存游标与快照；推送失败/模型失败/证据不符不推进游标，下轮重试。发送成功到写入游标之间若进程崩溃，可能重复通知一次（at-least-once）。
- 本地最多读取2MiB快照；变化超过20000字符则报错并保留游标，请在changedetection.io端配置CSS/文本筛选。通知展示最多3500字符差异，完整快照保留在原服务。
- 默认每300秒检查已有快照（最小30秒）；抓取频率由changedetection.io控制。无变化时保持安静。
- 默认不允许任何会话/域名；API Key只发给配置的服务，不跟随HTTP重定向。密钥不要写进仓库或聊天。
- 模型收到监测条件和变化文本；通知仅发送到创建监测的会话。群聊通知对群成员可见。

## Validation
`pip install pytest pytest-asyncio httpx ruff`; `pytest -q`; `ruff check .`.
HTTP contract tests use current official `/api/v1/watch`, history and snapshot endpoints with a deterministic transport. They test baseline, irrelevant changes, evidence-bearing matches, deduplication, failure retry, ownership and redirect rejection. Adapter tests run against actual AstrBot APIs with a mocked provider/sender; they skip when AstrBot is unavailable. No external account or paid model was used in tests. Configure your own instance before use.
