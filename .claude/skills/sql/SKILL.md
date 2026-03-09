---
name: sql
description: Query and manage SQLite, PostgreSQL, and MySQL databases. Use when the user wants to query data, inspect schemas, or run database operations.
metadata: {"ccbot":{"emoji":"🗄️","requires":{"bins":["sqlite3"]}}}
---

# SQL Skill

## SQLite (no server needed)

```bash
# One-off query
sqlite3 database.db "SELECT * FROM users LIMIT 10;"

# Pretty output
sqlite3 -column -header database.db "SELECT name, email FROM users WHERE active=1;"

# Import CSV
sqlite3 database.db <<'SQL'
.mode csv
.import data.csv my_table
SQL

# Export to CSV
sqlite3 -csv -header database.db "SELECT * FROM orders;" > output/orders.csv

# Inspect schema
sqlite3 database.db ".schema"
sqlite3 database.db ".tables"
```

## SQLite via Python (pandas integration)

```bash
uv run --with pandas python3 - <<'EOF'
import sqlite3, pandas as pd

conn = sqlite3.connect("database.db")

# Run query into DataFrame
df = pd.read_sql_query("""
    SELECT u.name, COUNT(o.id) as order_count, SUM(o.total) as revenue
    FROM users u
    LEFT JOIN orders o ON u.id = o.user_id
    GROUP BY u.id
    ORDER BY revenue DESC
    LIMIT 20
""", conn)

print(df.to_string(index=False))

# Save results
df.to_csv("output/report.csv", index=False)
print("\nSaved: output/report.csv")
conn.close()
EOF
```

## PostgreSQL

```bash
# Requires: psql installed, DATABASE_URL set
export PGPASSWORD="$DB_PASSWORD"

# Run query
psql "$DATABASE_URL" -c "SELECT count(*) FROM users;"

# Run from file
psql "$DATABASE_URL" -f query.sql

# Export to CSV
psql "$DATABASE_URL" -c "\COPY (SELECT * FROM orders) TO 'output/orders.csv' CSV HEADER;"

# Interactive session
psql "$DATABASE_URL"
```

## MySQL / MariaDB

```bash
mysql -h"$DB_HOST" -u"$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" \
  -e "SELECT COUNT(*) FROM users;"

# Dump database
mysqldump -h"$DB_HOST" -u"$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" > output/backup.sql
```

## Common SQL Patterns

### Aggregation & Grouping

```sql
SELECT
    DATE(created_at) as day,
    COUNT(*) as new_users,
    COUNT(*) FILTER (WHERE plan = 'pro') as pro_users
FROM users
WHERE created_at >= date('now', '-30 days')
GROUP BY 1
ORDER BY 1;
```

### Find Duplicates

```sql
SELECT email, COUNT(*) as cnt
FROM users
GROUP BY email
HAVING cnt > 1
ORDER BY cnt DESC;
```

### Window Functions

```sql
SELECT
    user_id,
    amount,
    SUM(amount) OVER (PARTITION BY user_id ORDER BY created_at) as running_total,
    ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at) as order_num
FROM purchases;
```

### JSON Fields (SQLite 3.38+ / PostgreSQL)

```sql
-- SQLite
SELECT json_extract(metadata, '$.plan') as plan FROM users;

-- PostgreSQL
SELECT metadata->>'plan' as plan FROM users;
```

## Schema Inspection

```bash
# SQLite: full schema
sqlite3 db.sqlite ".dump" | grep "^CREATE"

# PostgreSQL: table sizes
psql "$DATABASE_URL" -c "
SELECT relname as table, pg_size_pretty(pg_total_relation_size(oid)) as size
FROM pg_class WHERE relkind='r' ORDER BY pg_total_relation_size(oid) DESC LIMIT 20;
"
```

## Tips

- Always use parameterized queries in code (never string-concat with user input).
- Use `EXPLAIN QUERY PLAN` (SQLite) or `EXPLAIN ANALYZE` (PostgreSQL) to debug slow queries.
- For large exports, use `COPY` (PostgreSQL) or chunked pagination rather than `SELECT *`.
- Output CSVs/reports to `output/` for Feishu delivery.
