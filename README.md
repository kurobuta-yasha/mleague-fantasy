# Mリーグ チーム選択ゲーム 集計

14人の参加者がそれぞれMリーグから3チームを選び、今レギュラーシーズン（2026-27）の
3チーム合計ポイントで順位を競うゲームの集計ツール。

## ルール

- 参加者: 14人、各自Mリーグのチームを3つ選ぶ
- スコア = 選んだ3チームのレギュラーシーズン累計ポイントの合計
- 合計が多い人が上位。同点は同順位（1,1,3 方式）

## しくみ

```
m-league.jp トップの「ランキング」 ──(GitHub Actions / 毎日深夜)──▶ data/standings.json
                                                                   data/history.json
data/picks.json（誰がどのチーム） ─┐
                                   ├─▶ site/index.html がブラウザで合計・ソート ─▶ GitHub Pages
data/standings.json ───────────────┘
```

- 取得: `scripts/fetch_standings.py`（標準ライブラリのみ）。公式トップの
  レギュラーシーズンのランキング表からチーム名・累計pt・試合数を読む
- 実行タイミング: JST 00:30 / 03:00 / 07:00 の毎日。変更がなければコミットしない
- 順位は表示のたびに計算するので、ポイントが入れ替われば自動で並び替わる
- `data/history.json` に日ごとのスナップショットを残し、前回からの順位変動（▲▼）を出す
- 10チーム揃わない・未知のチーム名など、公式サイトの構造が変わった疑いがあるときは
  データを書き換えずに失敗する（Actions が赤くなる → GitHubから通知メールが来る）

## ディレクトリ

- `data/picks.json` — 参加者と選択チーム（手で編集する唯一のファイル）
- `data/teams.json` — チームの正式名・略称・表記ゆれ
- `data/standings.json` / `data/history.json` — 自動生成
- `scripts/` — 取得スクリプト、サイトのビルド
- `site/` — スマホ向けの表示ページ

## よくある操作

- 手動で今すぐ更新: GitHub の Actions → Update standings → Run workflow
- 参加者やチームを変える: `data/picks.json` を編集して main に push（自動で再デプロイ）
- ローカル確認:

  ```sh
  python3 scripts/fetch_standings.py && ./scripts/build_site.sh
  python3 -m http.server 8765 --directory _site
  ```
