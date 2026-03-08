---
name: weather
description: Get current weather and forecasts (no API key required).
metadata: {"ccbot":{"emoji":"🌤️","requires":{"bins":["curl"]}}}
---

# Weather

Two free services, no API keys needed. Use the `Bash` tool.

## wttr.in (primary)

```bash
curl -s "wttr.in/London?format=3"
curl -s "wttr.in/London?format=%l:+%c+%t+%h+%w"
curl -s "wttr.in/London?T"   # full forecast
```

Format codes: `%c` condition · `%t` temp · `%h` humidity · `%w` wind
Tips: URL-encode spaces (`New+York`), airport codes (`JFK`), units `?m`/`?u`

## Open-Meteo (fallback, JSON)

```bash
curl -s "https://api.open-meteo.com/v1/forecast?latitude=51.5&longitude=-0.12&current_weather=true"
```
