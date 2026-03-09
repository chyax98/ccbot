---
name: feishu-api
description: Interact with Feishu (Lark) API directly — read/write docs, send messages, manage bitable, query user info. Use when the user wants to automate Feishu operations.
metadata: {"ccbot":{"emoji":"🪶","requires":{"bins":["curl","uv"]}}}
---

# Feishu API Skill

Uses Feishu Open Platform API. Credentials from environment:
- `FEISHU_APP_ID` — App ID
- `FEISHU_APP_SECRET` — App Secret

## Get Access Token

```bash
TOKEN=$(curl -s -X POST "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal" \
  -H "Content-Type: application/json" \
  -d "{\"app_id\":\"$FEISHU_APP_ID\",\"app_secret\":\"$FEISHU_APP_SECRET\"}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tenant_access_token'])")
echo "Token: ${TOKEN:0:20}..."
```

## Send Message to Chat

```bash
# Send text to a chat_id
curl -s -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "receive_id": "oc_xxxxxxxxxxxxx",
    "msg_type": "text",
    "content": "{\"text\":\"Hello from ccbot!\"}"
  }' | python3 -m json.tool
```

## Read Feishu Doc / Wiki

```bash
# Get wiki node info
curl -s "https://open.feishu.cn/open-apis/wiki/v2/spaces/list" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Get doc content (raw blocks)
curl -s "https://open.feishu.cn/open-apis/docx/v1/documents/DOC_TOKEN/raw_content" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['content'])"
```

## Bitable (多维表格) Operations

```bash
# List records
curl -s "https://open.feishu.cn/open-apis/bitable/v1/apps/APP_TOKEN/tables/TABLE_ID/records" \
  -H "Authorization: Bearer $TOKEN" \
  -G --data-urlencode "page_size=100" \
  | python3 -m json.tool

# Create record
curl -s -X POST \
  "https://open.feishu.cn/open-apis/bitable/v1/apps/APP_TOKEN/tables/TABLE_ID/records" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "fields": {
      "标题": "新记录",
      "状态": "待处理",
      "日期": 1700000000000
    }
  }' | python3 -m json.tool

# Batch create (up to 500)
uv run --with lark-oapi python3 - <<'EOF'
import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
import os

client = lark.Client.builder() \
    .app_id(os.environ["FEISHU_APP_ID"]) \
    .app_secret(os.environ["FEISHU_APP_SECRET"]) \
    .build()

records = [
    AppTableRecord.builder().fields({"标题": f"记录{i}", "值": i * 10}).build()
    for i in range(1, 6)
]
req = BatchCreateAppTableRecordRequest.builder() \
    .app_token("APP_TOKEN") \
    .table_id("TABLE_ID") \
    .request_body(BatchCreateAppTableRecordRequestBody.builder().records(records).build()) \
    .build()

resp = client.bitable.v1.app_table_record.batch_create(req)
print(f"Created {len(resp.data.records)} records")
EOF
```

## Get User Info

```bash
# Lookup by email
curl -s -X POST "https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"emails":["user@example.com"]}' \
  | python3 -m json.tool
```

## Upload File & Get Key

```bash
FILE_KEY=$(curl -s -X POST "https://open.feishu.cn/open-apis/im/v1/files" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file_type=pdf" \
  -F "file_name=report.pdf" \
  -F "file=@output/report.pdf" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['file_key'])")

# Send file message
curl -s -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"receive_id\":\"oc_xxx\",\"msg_type\":\"file\",\"content\":\"{\\\"file_key\\\":\\\"$FILE_KEY\\\"}\"}"
```

## Python SDK (lark-oapi, already installed in ccbot)

```bash
uv run python3 - <<'EOF'
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
import os

client = lark.Client.builder() \
    .app_id(os.environ["FEISHU_APP_ID"]) \
    .app_secret(os.environ["FEISHU_APP_SECRET"]) \
    .log_level(lark.LogLevel.WARNING) \
    .build()

req = CreateMessageRequest.builder() \
    .receive_id_type("chat_id") \
    .request_body(
        CreateMessageRequestBody.builder()
        .receive_id("oc_xxxxx")
        .msg_type("text")
        .content('{"text":"Python SDK 发送"}')
        .build()
    ).build()

resp = client.im.v1.message.create(req)
print("Code:", resp.code, "Message:", resp.msg)
EOF
```

## Tips

- `lark-oapi` is already a ccbot dependency — use it for complex operations.
- Token expiry is 7200s — cache and refresh as needed.
- For bulk operations, always use batch APIs (up to 500 items).
- Rate limits: most APIs are 100 QPS per app.
