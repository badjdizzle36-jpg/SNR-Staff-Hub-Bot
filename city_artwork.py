"""Explicit business-to-poster geometry. Never infer artwork from DB order."""
import base64
import html
import json
from pathlib import Path

ROOT = Path(__file__).parent
POSTER = ROOT / 'city-run-board.jpg'
ART_DIR = ROOT / 'city-run-stickers'
# Coordinates in the supplied 1254px square board. Every business has exactly
# one visual tile and exactly one collectible identity.
SLOTS = {}
def add(key, box):
    SLOTS.setdefault(key, []).append(box)

top = ['food-1','food-2','food-3','food-4','food-5',
       'nightlife-1','nightlife-2','nightlife-3','nightlife-4','nightlife-5']
for i, key in enumerate(top):
    add(key, (174 + i*91, 16, 85, 145))
left = [f'mechanics-{i}' for i in range(1,7)] + [f'motors-{i}' for i in range(1,5)]
for i, key in enumerate(left):
    add(key, (20, 174 + i*93, 141, 88))
right = [f'shops-{i}' for i in range(1,6)] + [f'luxury-{i}' for i in range(1,4)]
for key, y, height in zip(right, [176,276,374,475,574,670,790,918], [94,91,91,87,87,109,119,132]):
    add(key, (1096,y,137,height))
bottom = ['finance-1','finance-2','finance-3','finance-4',
          'services-1','services-2','services-3','services-4','services-5','services-6']
for i, key in enumerate(bottom):
    add(key, (174 + i*91,1076,85,139))

def poster_markup(owned):
    """One unchanged poster with per-customer grayscale tile overlays."""
    owned = set(owned)
    overlays = []
    for key in sorted(set(owned)):
        for x,y,w,h in SLOTS.get(key, []):
            overlays.append(f'<svg class="poster-collected" x="{x}" y="{y}" width="{w}" height="{h}" viewBox="{x} {y} {w} {h}"><image href="/city-run-board-v2.png" width="1254" height="1254" filter="url(#collected-grey)"/><rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#000" opacity=".32"/><text x="{x+w/2}" y="{y+h/2}" text-anchor="middle" fill="white" font-size="28" font-family="Arial">✓</text><title>{html.escape(key)} collected</title></svg>')
    logo = ""  # Official logo is already part of the uploaded board.
    return ('<div class="city-board-art-wrap" data-board-build="poster-pro-1">'
            '<svg role="img" aria-label="SNR City Run board. Collected businesses are greyed out." viewBox="0 0 1254 1254" style="display:block;width:100%;height:auto">'
            '<defs><filter id="collected-grey"><feColorMatrix type="saturate" values="0"/></filter></defs>'
            '<image href="/city-run-board-v2.png" width="1254" height="1254"/>'
            + logo + ''.join(overlays) + '</svg><small>✓ Grey tiles are collected. Progress and claims below.</small></div>')

def sticker_svg(key, name):
    if key not in SLOTS:
        return None
    manifest_path = ART_DIR / 'business-index.json'
    if manifest_path.exists():
        entry = next((item for item in json.loads(manifest_path.read_text())
                      if item['business_id'] == key), None)
        if entry:
            asset = ART_DIR / Path(entry['file']).name
            if asset.is_file():
                data = base64.b64encode(asset.read_bytes()).decode('ascii')
                return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {int(entry["width"])} {int(entry["height"])}" role="img" aria-label="{html.escape(name, quote=True)}">'
                        f'<image href="data:image/png;base64,{data}" width="100%" height="100%" preserveAspectRatio="xMidYMid meet"/></svg>').encode()
    x,y,w,h = SLOTS[key][0]
    # Only the illustration: live name and rarity come from the database.
    art_height = h*.70
    data = base64.b64encode(POSTER.read_bytes()).decode('ascii')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x} {y} {w} {art_height}" role="img" aria-label="{html.escape(name, quote=True)}">'
            f'<image href="data:image/jpeg;base64,{data}" width="1254" height="1254"/></svg>').encode()
