#!/usr/bin/env bash
# Corpus: seed-oil health explainers. Six channels, same papers on screen, open disagreement.
# 3-min slice each, 720p, faststart mp4 (TwelveLabs rejects odd containers and YouTube URLs).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data

# id:start_sec — slice lands in the evidence discussion, not the intro
CLIPS=(
  "-xTaAHSFHUU:300"   # Nutrition Made Simple! — Are Seed Oils Inflammatory?! (The *Evidence* No One Shows)
  "CVACyQgN3ls:60"    # Huberman Lab Clips — The Truth About Seed Oils (Hyman & Huberman)
  "YvqzZw7GLrk:60"    # Dr Alo (cardiologist) — Are Seed Oils Inflammatory?
  "k6ts0e41pq8:300"   # Physionic — Seed Oils: The Raging Health Debate
  "efTBLsv4yYs:900"   # Adam Ragusea — The actual science of the "industrial seed oil" panic
  "h2YKBp4JUQo:240"   # Business Insider — Are Seed Oils Bad For You?
  "8ETN1lmMve4:60"    # Dr. Layne Norton — Seed Oils LOWER Inflammation?   <- direct opposite of the holdout
  "yGns_QvtAQQ:600"   # Ben Bikman — Seed Oils and Insulin Resistance      <- skeptic camp
)
# HOLDOUT, do not seed — this is the live-paste demo video:
#   FDIgoBusMxY  Dr. Eric Berg — "The #1 Most Dangerous Ingredient"
#   Paste it on stage and watch it conflict with Norton, Alo, and Nutrition Made Simple at once.
LEN=180

for c in "${CLIPS[@]}"; do
  id="${c%%:*}"; start="${c##*:}"; end=$((start + LEN))
  out="data/${id}.mp4"
  [ -f "$out" ] && { echo "skip $id"; continue; }
  echo "=== $id  ${start}-${end}s ==="
  # Full download, then clip locally. --download-sections makes ffmpeg fetch googlevideo
  # directly and YouTube 403s it; yt-dlp's own downloader carries the right headers.
  yt-dlp \
    -f "bv*[height<=480]+ba/b[height<=480]" --merge-output-format mp4 \
    -o "data/${id}.raw.%(ext)s" \
    --write-info-json \
    "https://www.youtube.com/watch?v=${id}" || { echo "FAILED $id"; continue; }
  ffmpeg -nostdin -loglevel error -y -ss "$start" -t "$LEN" -i "data/${id}.raw.mp4" \
    -c:v libx264 -crf 23 -preset veryfast -c:a aac -movflags +faststart "$out"
  rm -f "data/${id}.raw.mp4"
done

echo; echo "=== corpus ==="
for f in data/*.mp4; do
  printf "%s  %ss  %sMB\n" "$f" \
    "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" | cut -d. -f1)" \
    "$(du -m "$f" | cut -f1)"
done
