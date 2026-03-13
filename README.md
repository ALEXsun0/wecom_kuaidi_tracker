# wecom_kuaidi_tracker

企业微信微信客服 + 快递100 的最小可运行订阅服务。

流程：

1. 用户在微信客服里发送 `快递单号 + 手机号后四位`。
2. 服务通过企业微信客服回调拿到消息，再调用 `kf/sync_msg` 拉取内容。
3. 服务调用快递100订阅接口 `https://poll.kuaidi100.com/poll` 提交监控。
4. 快递100后续把物流变更推送到本服务的 `/callbacks/kuaidi100`。
5. 服务识别关键节点后，再通过 `kf/send_msg` 给用户发送提醒。

## 启动

1. 复制 `.env.example` 为 `.env`，补齐企业微信和快递100配置。
2. 配置企业微信客服回调地址：`GET/POST {BASE_URL}/callbacks/wecom`
3. 配置快递100订阅回调地址：`POST {BASE_URL}/callbacks/kuaidi100`
4. 运行：

```bash
python3 main.py
```

## 需要的环境变量

- `WECOM_CORP_ID` / `WECOM_CORP_SECRET`
- `WECOM_TOKEN` / `WECOM_ENCODING_AES_KEY`
- `KUAIDI100_KEY`
- `BASE_URL` 或 `KUAIDI100_CALLBACK_URL`

可选：

- `KUAIDI100_CUSTOMER`
说明：当前这版只走快递100订阅接口，`customer` 先保留给后续扩展主动查询接口 `query.do`。
- `KUAIDI100_SALT`
说明：设置后，快递100推送回调会按官方签名规则校验 `sign=MD5(param+salt+ts+key).upper()`。
- `KUAIDI100_DEFAULT_FROM` / `KUAIDI100_DEFAULT_TO`

## 当前实现的消息格式

用户发送以下任一格式即可：

```text
YT9693083639795 3975
单号: YT9693083639795 手机号后四位: 3975
单号: YT9693083639795 手机号后四位: 3975 公司: yuantong 发货地: 江门市 收货地: 深圳市
```

## 当前识别的关键节点

- 揽收 / 已发货
- 派送中
- 已签收
- 物流异常 / 退回 / 退签

## 限制

- 企业微信客服主动发消息受官方窗口限制：用户最近 48 小时内有消息，且最多主动发送 5 条。本服务会本地跟踪这个窗口，超出后不再推送。
- 这版使用系统自带 `openssl` 完成企业微信回调 AES 解密，没有引入第三方 Python 库。
- 数据存储使用本地 SQLite，适合单实例部署。
