#!/usr/bin/env python3
"""
Count Token - Track OpenClaw token usage per day

Reads token usage from the OpenClaw SQLite session store
(transcript_events.event_json -> message.usage) and aggregates
input/output tokens per day, then generates a monthly recap.

Since the SQLite migration (Oct 2026) sessions are no longer written
as JSONL files, so the legacy JSONL scan is only a fallback.
"""

import json
import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# Config
SESSIONS_DIR = Path("/root/.openclaw/agents/main/sessions")
AGENT_DB = Path("/root/.openclaw/agents/main/agent/openclaw-agent.sqlite")
DATA_DIR = Path("/root/clawd/count-token/data")
OUTPUT_DIR = Path("/root/clawd/count-token")
GITHUB_REPO = "jura39bot/count-token"

# Month names in French
MONTHS_FR = [
    "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
]


def _add_usage(daily_stats, timestamp, usage):
    """Fold one usage record into the daily aggregation."""
    if not timestamp or not usage:
        return
    date = timestamp[:10]
    daily_stats[date]["input"] += usage.get("input", 0)
    daily_stats[date]["output"] += usage.get("output", 0)
    daily_stats[date]["cache_read"] += usage.get("cacheRead", 0)
    daily_stats[date]["cache_write"] += usage.get("cacheWrite", 0)
    daily_stats[date]["requests"] += 1


def parse_sqlite():
    """Read token usage from the OpenClaw SQLite transcript store."""
    daily_stats = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "requests": 0})

    if not AGENT_DB.exists():
        return None

    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{AGENT_DB}?mode=ro", uri=True)
    except sqlite3.Error as e:
        print(f"⚠️  Cannot open {AGENT_DB}: {e}")
        return None

    try:
        rows = conn.execute(
            "select event_json from transcript_events where event_json like '%\"usage\"%'"
        )
        for (event_json,) in rows:
            try:
                record = json.loads(event_json)
            except json.JSONDecodeError:
                continue
            if record.get("type") != "message":
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            _add_usage(daily_stats, record.get("timestamp"), message.get("usage"))
    except sqlite3.Error as e:
        print(f"⚠️  SQLite read failed: {e}")
        return None
    finally:
        conn.close()

    return daily_stats


def parse_sessions():
    """Parse legacy session JSONL files (fallback / pre-migration history)."""
    daily_stats = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "requests": 0})

    if not SESSIONS_DIR.exists():
        print(f"❌ Sessions directory not found: {SESSIONS_DIR}")
        return daily_stats

    for session_file in SESSIONS_DIR.glob("*"):
        if not session_file.is_file():
            continue
        name = session_file.name

        # Skip checkpoint / trajectory path stubs
        if ".checkpoint." in name or name.endswith(".trajectory-path.json"):
            continue

        # Only process jsonl-derived files (plain, .deleted.*, .reset.*, .zst variants)
        if ".jsonl" not in name:
            continue

        try:
            if name.endswith(".zst"):
                proc = subprocess.run(
                    ["zstdcat", str(session_file)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if proc.returncode != 0:
                    print(f"⚠️  zstdcat failed on {session_file.name}: {proc.stderr.strip()[:200]}")
                    continue
                lines = proc.stdout.splitlines()
            else:
                with open(session_file, "r") as f:
                    lines = f.readlines()

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("type") != "message":
                        continue

                    message = record.get("message", {})
                    if not isinstance(message, dict):
                        continue

                    usage = message.get("usage")
                    if not usage:
                        continue

                    timestamp = record.get("timestamp")
                    if not timestamp:
                        continue

                    _add_usage(daily_stats, timestamp, usage)

                except (json.JSONDecodeError, KeyError):
                    continue
        except Exception as e:
            print(f"⚠️  Error reading {session_file}: {e}")
            continue

    return daily_stats


def load_existing_csv():
    """Load previously saved daily rows so history predating the store is kept."""
    csv_file = DATA_DIR / "daily_tokens.csv"
    existing = {}
    if not csv_file.exists():
        return existing
    with open(csv_file) as f:
        next(f, None)
        for line in f:
            parts = line.strip().split(",")
            if len(parts) < 7:
                continue
            try:
                existing[parts[0]] = {
                    "input": int(parts[1]),
                    "output": int(parts[2]),
                    "cache_read": int(parts[3]),
                    "cache_write": int(parts[4]),
                    "requests": int(parts[6]),
                }
            except ValueError:
                continue
    return existing


def save_daily_stats(daily_stats):
    """Save daily stats to CSV."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    all_dates = sorted(daily_stats.keys())
    if not all_dates:
        print("❌ No data found")
        return

    csv_file = DATA_DIR / "daily_tokens.csv"
    with open(csv_file, "w") as f:
        f.write("date,input,output,cache_read,cache_write,total_tokens,requests\n")
        for date in all_dates:
            stats = daily_stats[date]
            total = stats["input"] + stats["output"]
            f.write(f"{date},{stats['input']},{stats['output']},{stats['cache_read']},{stats['cache_write']},{total},{stats['requests']}\n")

    print(f"✅ Saved {len(all_dates)} days to {csv_file}")
    return csv_file


def generate_monthly_recap(daily_stats):
    """Generate monthly recap and save to file."""
    if not daily_stats:
        return None

    # Group by month
    monthly = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "days": set(), "requests": 0})

    for date, stats in daily_stats.items():
        if len(date) >= 7:
            month_key = date[:7]  # YYYY-MM
            monthly[month_key]["input"] += stats["input"]
            monthly[month_key]["output"] += stats["output"]
            monthly[month_key]["cache_read"] += stats["cache_read"]
            monthly[month_key]["cache_write"] += stats["cache_write"]
            monthly[month_key]["days"].add(date)
            monthly[month_key]["requests"] += stats["requests"]

    # Sort months
    sorted_months = sorted(monthly.keys(), reverse=True)

    recap_lines = []
    recap_lines.append("# 📊 Récapitulatif Token OpenClaw - Mensuel\n")
    recap_lines.append(f"*Généré le: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")

    for month_key in sorted_months:
        year, month_num = month_key.split("-")
        month_name = MONTHS_FR[int(month_num)]
        stats = monthly[month_key]

        total_input = stats["input"]
        total_output = stats["output"]
        total_tokens = total_input + total_output

        recap_lines.append(f"\n## {month_name} {year}\n")
        recap_lines.append(f"- **Input tokens**: {total_input:,}")
        recap_lines.append(f"- **Output tokens**: {total_output:,}")
        recap_lines.append(f"- **Total tokens**: {total_tokens:,}")
        recap_lines.append(f"- **Cache read**: {stats['cache_read']:,}")
        recap_lines.append(f"- **Cache write**: {stats['cache_write']:,}")
        recap_lines.append(f"- **Jours actifs**: {len(stats['days'])}")
        recap_lines.append(f"- **Requêtes**: {stats['requests']:,}")

    recap_file = OUTPUT_DIR / "MONTHLY_RECAP.md"
    with open(recap_file, "w") as f:
        f.write("\n".join(recap_lines))

    print(f"✅ Monthly recap saved to {recap_file}")
    return recap_file


def print_today_stats(daily_stats):
    """Print today's stats."""
    today = datetime.now().strftime("%Y-%m-%d")

    if today in daily_stats:
        stats = daily_stats[today]
        total = stats["input"] + stats["output"]
        print(f"\n📅 Aujourd'hui ({today})")
        print(f"   Input:  {stats['input']:,}")
        print(f"   Output: {stats['output']:,}")
        print(f"   Total:  {total:,}")
        print(f"   Requêtes: {stats['requests']}")
    else:
        print(f"\n📅 Aujourd'hui ({today}) - Pas de données")


def main():
    print("🔄 Parsing OpenClaw sessions...")

    daily_stats = parse_sqlite()
    source = "SQLite"
    if daily_stats is None:
        print("⚠️  SQLite store unavailable, falling back to JSONL files")
        daily_stats = parse_sessions()
        source = "JSONL"

    # Legacy JSONL history: fill dates the live store does not cover
    legacy = parse_sessions()
    for date, stats in legacy.items():
        if date not in daily_stats:
            daily_stats[date] = stats

    # Preserve older CSV history (before the store existed)
    for date, stats in load_existing_csv().items():
        if date not in daily_stats:
            daily_stats[date] = stats

    if not daily_stats:
        print("❌ No token usage data found")
        return 1

    print(f"📊 Found data for {len(daily_stats)} days (source: {source})")

    # Save daily CSV
    save_daily_stats(daily_stats)

    # Generate monthly recap
    generate_monthly_recap(daily_stats)

    # Print today's stats
    print_today_stats(daily_stats)

    # Print last 7 days summary
    sorted_dates = sorted(daily_stats.keys(), reverse=True)[:7]
    print("\n📆 7 derniers jours:")
    for date in sorted_dates:
        stats = daily_stats[date]
        total = stats["input"] + stats["output"]
        print(f"   {date}: {total:,} tokens ({stats['requests']} req)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
