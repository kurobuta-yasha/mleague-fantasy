#!/bin/sh
# site/ と data/ を _site/ にまとめる（GitHub Pages 配信用 / ローカル確認用）
set -eu
cd "$(dirname "$0")/.."
rm -rf _site
mkdir -p _site/data
cp site/* _site/
cp data/*.json _site/data/
touch _site/.nojekyll
