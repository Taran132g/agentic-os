#!/bin/zsh
# One-shot: render LOOSE07 + LOOSE08 once ElevenLabs quota is back
# (fallback key refills Sun 7/19 ~11pm; or add a fresh key to .env first).
# Pools are pre-carved & isolated: D is live now, E swaps in after 07.
set -e
cd ~/agentic_os
set -a; source .env; set +a
DIR=broll_cache/minecraft_dantdm

echo "=== LOOSE07 (pool D · married life up piano) ==="
RANDOM_TEMPLATE=0 python3 content_pipeline.py growth_loose07.txt
mkdir -p "$DIR/used_loose07"
mv "$DIR"/dantdm_hcD*.mp4 "$DIR/used_loose07/"

echo "=== LOOSE08 (pool E · narvent fainted) ==="
cp "$DIR"/reserve_poolE/dantdm_hcE*.mp4 "$DIR/" 2>/dev/null || true
[ -f "$DIR/dantdm_hcE1.mp4" ] || { echo "pool E missing — re-carve: raw spans 1560-1610 + 1610-1645"; exit 1; }
RANDOM_TEMPLATE=0 python3 content_pipeline.py growth_loose08.txt
mkdir -p "$DIR/used_loose08"
mv "$DIR"/dantdm_hcE*.mp4 "$DIR/used_loose08/"

echo "DONE — renders in ~/Desktop/Stoic Renders/. Next: update growth_tapes.json + draft to TikTok."

