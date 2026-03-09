---
name: data-analysis
description: Analyze CSV, JSON, Excel data; generate charts and reports. Use when the user provides data files or asks for statistics, charts, or data insights.
metadata: {"ccbot":{"emoji":"📊","requires":{"bins":["uv"]}}}
---

# Data Analysis Skill

Use `uv run` with inline dependencies — no venv setup needed.

## Quick Stats on CSV

```bash
uv run --with pandas python3 - <<'EOF'
import pandas as pd, sys
df = pd.read_csv(sys.argv[1])
print(df.shape)
print(df.dtypes)
print(df.describe())
print("\nMissing values:")
print(df.isnull().sum())
EOF data.csv
```

## Load JSON / Excel

```bash
# JSON
uv run --with pandas python3 -c "
import pandas as pd
df = pd.read_json('data.json')
print(df.head())
"

# Excel
uv run --with pandas,openpyxl python3 -c "
import pandas as pd
df = pd.read_excel('data.xlsx', sheet_name=0)
print(df.head())
"
```

## Generate Charts (output to Feishu)

```bash
mkdir -p output

uv run --with pandas,matplotlib python3 - <<'EOF'
import pandas as pd, matplotlib.pyplot as plt, sys

df = pd.read_csv(sys.argv[1])

# 折线图
fig, ax = plt.subplots(figsize=(10, 5))
df.set_index(df.columns[0])[df.columns[1]].plot(ax=ax, title=sys.argv[1])
plt.tight_layout()
plt.savefig("output/chart.png", dpi=150)
print("Saved: output/chart.png")
EOF data.csv
```

## Advanced: Multi-chart Report

```bash
uv run --with pandas,matplotlib,seaborn python3 - <<'EOF'
import pandas as pd, matplotlib.pyplot as plt, seaborn as sns

df = pd.read_csv("data.csv")
fig, axes = plt.subplots(2, 2, figsize=(14, 10))
fig.suptitle("Data Report", fontsize=16)

# Distribution
df[df.select_dtypes("number").columns[0]].hist(ax=axes[0,0], bins=20)
axes[0,0].set_title("Distribution")

# Correlation heatmap
sns.heatmap(df.select_dtypes("number").corr(), ax=axes[0,1], annot=True, fmt=".2f")
axes[0,1].set_title("Correlation")

# Top categories
if df.select_dtypes("object").shape[1] > 0:
    cat_col = df.select_dtypes("object").columns[0]
    df[cat_col].value_counts().head(10).plot(kind="bar", ax=axes[1,0])
    axes[1,0].set_title(f"Top {cat_col}")

plt.tight_layout()
plt.savefig("output/report.png", dpi=150, bbox_inches="tight")
print("Saved: output/report.png")
EOF
```

## Filter & Transform

```bash
uv run --with pandas python3 - <<'EOF'
import pandas as pd

df = pd.read_csv("data.csv")

# 过滤
filtered = df[df["status"] == "active"]

# 分组聚合
summary = df.groupby("category").agg({"value": ["sum", "mean", "count"]})

# 输出
summary.to_csv("output/summary.csv")
print(summary.to_string())
EOF
```

## Tips

- Always print `df.head()` first to understand the data shape.
- For large files (>100MB), read in chunks: `pd.read_csv(f, chunksize=10000)`.
- Charts go to `output/` for automatic Feishu delivery.
- For interactive analysis, use the `tmux` skill to run a Jupyter notebook.
