---
name: web-search
description: Search the web for real-time information using multiple free services. Use when the user asks about current events, facts, documentation, or anything requiring live data.
metadata: {"ccbot":{"emoji":"🔍","requires":{"bins":["curl"]}}}
---

# Web Search

Use the `WebSearch` tool (preferred) or `curl` fallbacks to retrieve real-time information.

## Primary: WebSearch Tool

```
WebSearch("query here")
```

Best for general queries, news, documentation. Returns structured snippets.

## Secondary: WebFetch on Specific Sites

```
WebFetch("https://docs.python.org/3/library/asyncio.html", "how to use asyncio.gather")
WebFetch("https://github.com/owner/repo/issues", "open issues about X")
```

## Fallback: curl-based Search

### DuckDuckGo Instant Answer (JSON)

```bash
curl -sG "https://api.duckduckgo.com/" \
  --data-urlencode "q=Python asyncio gather" \
  -d "format=json" -d "no_html=1" -d "skip_disambig=1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('AbstractText') or d.get('Answer') or 'No answer')"
```

### SearXNG (JSON API, multiple public instances)

```bash
# 选一个可用实例
SEARX="https://searx.be"
curl -sG "$SEARX/search" \
  --data-urlencode "q=query" \
  -d "format=json" -d "categories=general" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data['results'][:5]:
    print(r['title'])
    print(r['url'])
    print(r.get('content','')[:200])
    print()
"
```

### Brave Search (needs `BRAVE_API_KEY` env var)

```bash
curl -s "https://api.search.brave.com/res/v1/web/search?q=query&count=5" \
  -H "X-Subscription-Token: $BRAVE_API_KEY" \
  -H "Accept: application/json" \
  | python3 -c "
import sys,json
for r in json.load(sys.stdin)['web']['results']:
    print(r['title'], r['url'])
"
```

## Tips

- Always cite sources when presenting search results.
- For technical docs, prefer WebFetch on the official site over search snippets.
- For GitHub issues/PRs, use the `github` skill.
- For video content, use the `summarize` skill.
