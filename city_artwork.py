"""Explicit business-to-poster geometry. Never infer artwork from DB order."""
import base64
import html
import json
from pathlib import Path

ROOT = Path(__file__).parent
POSTER = ROOT / 'city-run-board-v4.png'
ART_DIR = ROOT / 'city-run-stickers'
# Coordinates in the exact 1254px one-UwU board.  Each box deliberately covers
# the full printed tile (including its coloured frame), so a collected business
# cannot leave a coloured edge behind.
SLOTS = {}
def add(key, box):
    SLOTS.setdefault(key, []).append(box)

def add_edge(keys, edges, *, horizontal, fixed_start, fixed_end):
    """Map the real, non-uniform printed cells rather than an estimated grid."""
    for key, start, end in zip(keys, edges, edges[1:]):
        if horizontal:
            add(key, (start, fixed_start, end-start, fixed_end-fixed_start))
        else:
            add(key, (fixed_start, start, fixed_end-fixed_start, end-start))

top = ['food-1','food-2','food-3','food-4','food-5',
       'nightlife-1','nightlife-2','nightlife-3','nightlife-4','nightlife-5']
top_edges = [169, 263, 354, 443, 533, 627, 716, 807, 897, 989, 1086]
add_edge(top, top_edges, horizontal=True, fixed_start=8, fixed_end=168)
left = [f'mechanics-{i}' for i in range(1,7)] + [f'motors-{i}' for i in range(1,5)]
left_edges = [169, 271, 369, 467, 565, 657, 751, 844, 940, 1004, 1072]
add_edge(left, left_edges, horizontal=False, fixed_start=8, fixed_end=168)
right = [f'shops-{i}' for i in range(1,6)] + [f'luxury-{i}' for i in range(1,4)]
right_edges = [169, 271, 369, 467, 566, 665, 781, 916, 1072]
add_edge(right, right_edges, horizontal=False, fixed_start=1086, fixed_end=1246)
# The supplied artwork prints Search & Rescue twice.  Both visual positions map
# to the same services-4 record, so there are still only 38 collectibles.
bottom = ['finance-1','finance-2','finance-3','finance-4',
          'services-1','services-2','services-3','services-4','services-4','services-5','services-6']
bottom_edges = [169, 259, 343, 425, 506, 587, 671, 755, 838, 921, 1004, 1086]
add_edge(bottom, bottom_edges, horizontal=True, fixed_start=1066, fixed_end=1246)

def poster_markup(owned):
    """One unchanged poster with per-customer grayscale tile overlays."""
    owned = set(owned)
    definitions = []
    overlays = []
    for key in sorted(set(owned)):
        for index, (x,y,w,h) in enumerate(SLOTS.get(key, [])):
            clip_id = f'collected-{key}-{index}'
            definitions.append(
                f'<clipPath id="{clip_id}"><rect x="{x}" y="{y}" width="{w}" height="{h}"/></clipPath>')
            cx, cy = x + w/2, y + h/2
            overlays.append(
                f'<g class="poster-collected" clip-path="url(#{clip_id})">'
                '<image href="/city-run-board-v4.png" x="0" y="0" width="1254" height="1254" '
                'filter="url(#collected-grey)" preserveAspectRatio="none"/>'
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#050505" opacity=".50"/>'
                f'<title>{html.escape(key)} collected</title></g>'
                f'<g class="poster-tick" transform="translate({cx:g} {cy:g})" aria-hidden="true">'
                '<path d="M -13 0 L -4 10 L 14 -11" fill="none" stroke="#080808" stroke-width="10" '
                'stroke-linecap="round" stroke-linejoin="round" opacity=".78"/>'
                '<path d="M -13 0 L -4 10 L 14 -11" fill="none" stroke="#fff" stroke-width="5" '
                'stroke-linecap="round" stroke-linejoin="round"/></g>')
    logo = ""  # Official logo is already part of the uploaded board.
    return ('<div class="city-board-art-wrap" data-board-build="poster-pro-4">'
            '<svg role="img" aria-label="SNR City Run board. Collected businesses are greyed out." viewBox="0 0 1254 1254" style="display:block;width:100%;height:auto">'
            '<defs><filter id="collected-grey" color-interpolation-filters="sRGB"><feColorMatrix type="saturate" values="0"/></filter>'
            + ''.join(definitions) + '</defs>'
            '<image href="/city-run-board-v4.png" width="1254" height="1254"/>'
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
