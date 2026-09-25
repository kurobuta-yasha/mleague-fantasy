#!/usr/bin/env python3
"""m-league.jp トップページの「ランキング（レギュラー）」からチーム累計ポイントを取得し、
data/standings.json と data/history.json を更新する。

- 標準ライブラリのみ（GitHub Actions でそのまま動く）
- 10チーム揃わない・未知のチーム名があるなど、ページ構造が変わった疑いがある場合は
  既存データを書き換えずに非0で終了する
- `--html FILE` でローカルのHTMLをパースできる（テスト用）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

SOURCE_URL = "https://m-league.jp/"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
JST = timezone(timedelta(hours=9))


def load_team_index() -> dict[str, str]:
    """正式名・略称・別名 → 正式名 の辞書"""
    teams = json.loads((DATA / "teams.json").read_text(encoding="utf-8"))["teams"]
    index: dict[str, str] = {}
    for t in teams:
        for key in [t["name"], t["short"], *t.get("aliases", [])]:
            index[normalize(key)] = t["name"]
    return index


def normalize(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def parse_point(text: str) -> float:
    t = text.strip().replace("pt", "").replace(",", "")
    t = t.replace("▲", "-").replace("−", "-").replace("－", "-").replace("+", "")
    return float(t)


def fetch_html() -> str:
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "Mozilla/5.0 (mleague-fantasy standings bot)"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8")


def parse_standings(html: str, team_index: dict[str, str]) -> list[dict]:
    start = html.find("p-ranking__team-list -regular")
    if start < 0:
        raise ValueError("レギュラーシーズンのランキング表が見つかりません")
    end = html.find("</ol>", start)
    block = html[start:end]

    rows = []
    for item in block.split('class="p-ranking__team-item"')[1:]:
        alt = re.search(r'alt="([^"]+)"', item)
        pt = re.search(r'p-ranking__current-point">\s*([^<]+?)\s*<', item)
        games = re.search(r'p-ranking__game-count">\s*(\d+)\s*/\s*(\d+)', item)
        if not (alt and pt):
            raise ValueError(f"チーム行のパースに失敗: {item[:200]!r}")
        raw_name = unescape(alt.group(1))
        name = team_index.get(normalize(raw_name))
        if name is None:
            raise ValueError(f"未知のチーム名: {raw_name}")
        rows.append(
            {
                "name": name,
                "point": parse_point(pt.group(1)),
                "games": int(games.group(1)) if games else None,
                "games_total": int(games.group(2)) if games else None,
            }
        )

    expected = len(set(team_index.values()))
    if len(rows) != expected or len({r["name"] for r in rows}) != expected:
        raise ValueError(f"{expected}チーム分取れていません（{len(rows)}件）")
    return rows


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_history(rows: list[dict], now: datetime) -> bool:
    """ポイントが前回スナップショットから変わっていれば追記する。追記したら True"""
    path = DATA / "history.json"
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    points = {r["name"]: r["point"] for r in rows}
    if history and history[-1]["points"] == points:
        return False
    snapshot = {
        "at": now.isoformat(timespec="seconds"),
        "games": max((r["games"] or 0) for r in rows),
        "points": points,
    }
    # 同じ日（JST）のスナップショットは上書きして1日1件にする
    if history and history[-1]["at"][:10] == snapshot["at"][:10]:
        history[-1] = snapshot
    else:
        history.append(snapshot)
    write_json(path, history)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="ネットから取らずにこのHTMLファイルをパースする")
    args = ap.parse_args()

    html = Path(args.html).read_text(encoding="utf-8") if args.html else fetch_html()
    try:
        rows = parse_standings(html, load_team_index())
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    now = datetime.now(JST)
    standings_path = DATA / "standings.json"
    prev = (
        json.loads(standings_path.read_text(encoding="utf-8"))
        if standings_path.exists()
        else None
    )
    if prev and prev["teams"] == rows:
        print("変更なし")
        return 0

    write_json(
        standings_path,
        {"updated_at": now.isoformat(timespec="seconds"), "source": SOURCE_URL, "teams": rows},
    )
    update_history(rows, now)
    for r in rows:
        print(f'{r["name"]:<16} {r["point"]:>8.1f}pt  {r["games"]}/{r["games_total"]}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
