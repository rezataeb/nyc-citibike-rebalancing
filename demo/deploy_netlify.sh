#!/bin/bash
# Deploys the video tour page (DOT video) to https://nyc-citibike-rebalancing.netlify.app.
# Same page as /video.html on Vercel (its links are absolute, so it works on
# both): index.html = video.html, plus media/. Replaced the old launch clip
# (template.html / build.py here) on 2026-11-03; Netlify keeps old deploys
# for rollback. The CLI wants the site ID, not the name.
set -e
cd "$(dirname "$0")/.."
SITE_ID=c4f4e4b0-8183-4b24-bcb2-181bf6b66452
OUT=$(mktemp -d)
# The Netlify page plays the DOT planners' video (the user's choice).
sed 's/<body data-video="hiring">/<body data-video="dot">/' video.html > "$OUT/index.html"
grep -q 'data-video="dot"' "$OUT/index.html"
mkdir "$OUT/media"
cp media/dot.mp4 media/dot_poster.jpg "$OUT/media/"
npx -y netlify-cli deploy --prod --dir "$OUT" --site "$SITE_ID" --message "Video tour page"
