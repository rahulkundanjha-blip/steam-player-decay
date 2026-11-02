#!/usr/bin/env bash
# Downloads the public source file into data/. It isn't redistributed here.
set -euo pipefail
cd "$(dirname "$0")/../data"
curl -fsSL -o steam_releases.csv "https://raw.githubusercontent.com/rfordatascience/tidytuesday/f422450f6519830f6ce0312e5294e3cf110c72ed/data/2019/2019-07-30/video_games.csv"
curl -fsSL -o steam_charts.csv "https://raw.githubusercontent.com/rfordatascience/tidytuesday/f422450f6519830f6ce0312e5294e3cf110c72ed/data/2021/2021-03-16/games.csv"
