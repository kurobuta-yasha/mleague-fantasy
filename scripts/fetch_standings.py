#!/usr/bin/env python3
"""m-league.jp からチームのレギュラーシーズン累計ポイントを取得し、
data/standings.json と data/history.json を更新する。

取得元（上から順に試す）:
1. 公式サイトのデータAPI `/api/mleague/data?kind=ranking`（2026-11 のサイト改修以降、
   公式ページ自体がこのAPIから描画している）
2. トップページHTMLの「ランキング（レギュラー）」。改修後は古いまま残っているので予備扱い

- 標準ライブラリのみ（GitHub Actions でそのまま動く）
- 10チーム揃わない・未知のチーム名がある・前回より試合数が減っている（古いデータ）など、
  おかしいと判断したら既存データを書き換えずに非0で終了する（Actions が失敗→メール通知）
- `--html FILE` / `--api-json FILE` でローカルのファイルをパースできる（テスト用）

あわせて同じデータAPIの `kind=stats` から選手個人成績を取り、data/players.json に書く。
個人成績の取得に失敗してもチーム成績の更新は止めない（ただし終了コードは非0にして通知する）。
`--stats-json FILE` でローカルのファイルをパースできる。
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
API_URL = "https://m-league.jp/api/mleague/data?kind=ranking"
STATS_URL = "https://m-league.jp/api/mleague/data?kind=stats"
REGULAR_GAMES = 120  # 1チームあたりのレギュラーシーズン試合数
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


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (mleague-fantasy standings bot)"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8")


def parse_api(data: dict, team_index: dict[str, str]) -> list[dict]:
    if data.get("stale"):
        raise ValueError("APIが stale=true を返しました")
    phase = (data.get("season") or {}).get("phase")
    if phase != "regular":
        raise ValueError(f"レギュラーシーズンのデータではありません（phase={phase}）")
    rows = []
    for t in data["ranking"]["teams"]:
        name = team_index.get(normalize(t["name"]))
        if name is None:
            raise ValueError(f"未知のチーム名: {t['name']}")
        rows.append(
            {
                "name": name,
                "point": round(float(t["points"]), 1),
                "games": int(t["games"]),
                "games_total": REGULAR_GAMES,
            }
        )
    check_rows(rows, team_index)
    return sorted(rows, key=lambda r: r["point"], reverse=True)


def check_rows(rows: list[dict], team_index: dict[str, str]) -> None:
    expected = len(set(team_index.values()))
    if len(rows) != expected or len({r["name"] for r in rows}) != expected:
        raise ValueError(f"{expected}チーム分取れていません（{len(rows)}件）")


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

    check_rows(rows, team_index)
    return rows


# APIの指標名 → players.json のキー
PLAYER_METRICS = {
    "試合数": "games",
    "総局数": "hands",
    "ポイント": "point",
    "平着": "avg_rank",
    "1位": "first",
    "2位": "second",
    "3位": "third",
    "4位": "fourth",
    "トップ率": "top_rate",
    "連対率": "rentai_rate",
    "ラス回避率": "last_avoid_rate",
    "ベストスコア": "best_score",
    "平均打点": "avg_win",
    "副露率": "call_rate",
    "リーチ率": "riichi_rate",
    "アガリ率": "win_rate",
    "放銃率": "deal_in_rate",
    "放銃平均打点": "avg_deal_in",
}
REQUIRED_METRICS = {"試合数", "ポイント", "平着", "1位", "2位", "3位", "4位"}


def parse_stats(data: dict, team_index: dict[str, str]) -> list[dict]:
    """`kind=stats` のレスポンスから選手ごとの成績を取り出す"""
    if data.get("stale"):
        raise ValueError("個人成績APIが stale=true を返しました")
    phase = (data.get("season") or {}).get("phase")
    if phase != "regular":
        raise ValueError(f"個人成績がレギュラーシーズンのデータではありません（phase={phase}）")
    players = []
    seen_teams = set()
    for t in data["stats"]["teams"]:
        team = team_index.get(normalize(t["team"]))
        if team is None:
            raise ValueError(f"個人成績に未知のチーム名: {t['team']}")
        seen_teams.add(team)
        metrics = {m["name"]: m["values"] for m in t["metrics"]}
        missing = REQUIRED_METRICS - metrics.keys()
        if missing:
            raise ValueError(f"個人成績の指標が足りません: {sorted(missing)}")
        for i, name in enumerate(t["players"]):
            row = {"name": name, "team": team}
            for src, key in PLAYER_METRICS.items():
                if src not in metrics:
                    continue
                v = metrics[src][i]
                if isinstance(v, float):
                    v = round(v, 1) if key == "point" else round(v, 4)
                row[key] = v
            players.append(row)
    if len(seen_teams) != len(set(team_index.values())):
        raise ValueError(f"個人成績が全チーム分取れていません（{len(seen_teams)}チーム）")
    return sorted(players, key=lambda r: r["point"], reverse=True)


def update_players(args, team_index: dict[str, str], now: datetime) -> bool:
    """data/players.json を更新する。失敗したら False（チーム成績の更新は続ける）"""
    try:
        if args.stats_json:
            data, source = json.loads(Path(args.stats_json).read_text(encoding="utf-8")), args.stats_json
        else:
            data, source = json.loads(fetch(STATS_URL)), STATS_URL
        players = parse_stats(data, team_index)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: 個人成績を取得できませんでした（{e}）", file=sys.stderr)
        return False

    path = DATA / "players.json"
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if prev and prev["players"] == players:
        print("個人成績: 変更なし")
        return True
    if prev:
        prev_games = sum(p.get("games") or 0 for p in prev["players"])
        new_games = sum(p.get("games") or 0 for p in players)
        if new_games < prev_games:
            print(f"ERROR: 個人成績の試合数が前回より少ない（{new_games} < {prev_games}）。古いデータとみなして更新しません", file=sys.stderr)
            return False
    write_json(path, {"updated_at": now.isoformat(timespec="seconds"), "source": source, "players": players})
    print(f"個人成績: {len(players)}人分を更新")
    return True


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


def load_rows(args, team_index: dict[str, str]) -> tuple[list[dict], str]:
    if args.html:
        return parse_standings(Path(args.html).read_text(encoding="utf-8"), team_index), args.html
    if args.api_json:
        return parse_api(json.loads(Path(args.api_json).read_text(encoding="utf-8")), team_index), args.api_json
    try:
        return parse_api(json.loads(fetch(API_URL)), team_index), API_URL
    except Exception as e:  # noqa: BLE001 — どんな失敗でも予備のHTMLを試す
        print(f"WARN: API から取得できませんでした（{e}）。トップページHTMLを試します", file=sys.stderr)
        return parse_standings(fetch(SOURCE_URL), team_index), SOURCE_URL


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="ネットから取らずにこのHTMLファイルをパースする")
    ap.add_argument("--api-json", help="ネットから取らずにこのAPIレスポンスJSONをパースする")
    ap.add_argument("--stats-json", help="ネットから取らずにこの個人成績APIレスポンスJSONをパースする")
    args = ap.parse_args()

    team_index = load_team_index()
    now = datetime.now(JST)
    players_ok = update_players(args, team_index, now)
    rc = 0 if players_ok else 1

    try:
        rows, source = load_rows(args, team_index)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    standings_path = DATA / "standings.json"
    prev = (
        json.loads(standings_path.read_text(encoding="utf-8"))
        if standings_path.exists()
        else None
    )
    if prev and prev["teams"] == rows:
        print("変更なし")
        return rc
    # 公式側が古いデータを返すことがある（2026-11 の改修時に実際に起きた）ので、試合数が減ったら採用しない
    if prev:
        prev_games = sum(t["games"] or 0 for t in prev["teams"])
        new_games = sum(r["games"] or 0 for r in rows)
        if new_games < prev_games:
            print(f"ERROR: 取得データの試合数が前回より少ない（{new_games} < {prev_games}）。古いデータとみなして更新しません", file=sys.stderr)
            return 1

    write_json(
        standings_path,
        {"updated_at": now.isoformat(timespec="seconds"), "source": source, "teams": rows},
    )
    update_history(rows, now)
    for r in rows:
        print(f'{r["name"]:<16} {r["point"]:>8.1f}pt  {r["games"]}/{r["games_total"]}')
    return rc


if __name__ == "__main__":
    sys.exit(main())
