---
name: http-client
description: Make HTTP requests, test REST APIs, handle auth, inspect responses. Use when the user wants to call an API, test an endpoint, or fetch web data.
metadata: {"ccbot":{"emoji":"🌐","requires":{"bins":["curl"]}}}
---

# HTTP Client Skill

Use `curl` via the `Bash` tool for all HTTP operations.

## GET Request

```bash
curl -s "https://api.example.com/users" | python3 -m json.tool
```

## POST with JSON Body

```bash
curl -s -X POST "https://api.example.com/items" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"name": "test", "value": 42}' \
  | python3 -m json.tool
```

## Upload File (multipart)

```bash
curl -s -X POST "https://api.example.com/upload" \
  -F "file=@/path/to/file.pdf" \
  -F "name=my-document" \
  -H "Authorization: Bearer $TOKEN"
```

## Download File

```bash
curl -sL "https://example.com/file.zip" -o output/file.zip
echo "Downloaded: $(du -sh output/file.zip | cut -f1)"
```

## Inspect Response Headers

```bash
curl -sI "https://example.com"          # headers only
curl -sv "https://example.com" 2>&1     # verbose (headers + body)
```

## Handle Pagination

```bash
uv run --with requests python3 - <<'EOF'
import requests, os

BASE = "https://api.example.com"
TOKEN = os.environ.get("API_TOKEN", "")
headers = {"Authorization": f"Bearer {TOKEN}"}

results = []
page = 1
while True:
    resp = requests.get(f"{BASE}/items", params={"page": page, "per_page": 100}, headers=headers)
    data = resp.json()
    items = data.get("items", [])
    results.extend(items)
    if not data.get("has_next"):
        break
    page += 1

print(f"Total fetched: {len(results)}")
import json
print(json.dumps(results[:3], indent=2, ensure_ascii=False))
EOF
```

## Test API Endpoint (health check)

```bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "https://api.example.com/health")
if [ "$STATUS" = "200" ]; then
    echo "✅ API healthy"
else
    echo "❌ API returned $STATUS"
fi
```

## GraphQL

```bash
curl -s -X POST "https://api.example.com/graphql" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "query": "{ user(id: \"123\") { name email } }"
  }' | python3 -m json.tool
```

## Common Headers Reference

| Header | Example |
|--------|---------|
| Auth Bearer | `-H "Authorization: Bearer $TOKEN"` |
| Auth Basic | `-u "user:pass"` |
| Content-Type JSON | `-H "Content-Type: application/json"` |
| Custom Header | `-H "X-API-Key: $KEY"` |
| Accept JSON | `-H "Accept: application/json"` |

## Tips

- Use `-s` (silent) to suppress progress bars.
- Use `-L` to follow redirects.
- Use `-w "%{http_code}"` to capture status code.
- For complex sessions, use Python `requests` with `uv run --with requests`.
- Secrets go in environment variables, never hardcoded.
