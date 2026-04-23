#!/usr/bin/env python3
"""
Count Token - Track OpenClaw token usage per day

This script parses OpenClaw session files and aggregates
input/output tokens per day, then generates a monthly recap.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# Config
SESSIONS_DIR = Path("/root/.openclaw/agents/main/sessions")
DATA_DIR = Path("/root/clawd/count-token/data")
OUTPUT_DIR = Path("/root/clawd/count-token")
GITHUB_REPO = "jura39bot/count-token"

# Month names in French
MONTHS_FR = [
    "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
]


def parse_sessions():
    """Parse all session JSONL files and extract token usage."""
    daily_stats = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "requests": 0})

    if not SESSIONS_DIR.exists():
        print(f"❌ Sessions directory not found: {SESSIONS_DIR}")
        return daily_stats

    for session_file in SESSIONS_DIR.glob("*.jsonl"):
        # Skip checkpoint files
        if ".checkpoint." in session_file.name:
            continue

        try:
            with open(session_file, "r") as f:
                for line in f:
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

                        # Extract date (YYYY-MM-DD)
                        date = timestamp[:10]

                        # Add to daily stats
                        daily_stats[date]["input"] += usage.get("input", 0)
                        daily_stats[date]["output"] += usage.get("output", 0)
                        daily_stats[date]["cache_read"] += usage.get("cacheRead", 0)
                        daily_stats[date]["cache_write"] += usage.get("cacheWrite", 0)
                        daily_stats[date]["requests"] += 1

                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception as e:
            print(f"⚠️ Error reading {session_file}: {e}")
            continue

    return daily_stats


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
    daily_stats = parse_sessions()

    if not daily_stats:
        print("❌ No token usage data found")
        return 1

    print(f"📊 Found data for {len(daily_stats)} days")

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