#!/usr/bin/env python3
"""Neura Atlas bridge edits to index.html, kept as (old, new) pairs in the format of opportunity_patches.py, so a
rebuild of the hosted base can re-apply them (docs/ATLAS_BRIDGE.md).

The bridge itself is js/atlas.js and data/atlas/; it changes nothing in js/app.js or js/data.js. js/atlas.js adds
its matrix row and wraps rebuildMatrix and renderFreshness at load, so the only edit to a generated file is the one
script tag below.

apply(text, PAIRS) -> text   (idempotent: a pair whose `new` is already present is skipped)
Run directly to patch the local working copy:  python3 deploy/atlas_patches.py
"""
import os
import sys

INDEX = [
    ('<script src="js/app.js?v=26"></script>\n',
     '<script src="js/app.js?v=26"></script>\n<script src="js/atlas.js?v=2"></script>\n'),
]


def apply(text, pairs):
    for old, new in pairs:
        if new in text:
            continue
        assert old in text, f'anchor missing: {old[:80]!r}'
        text = text.replace(old, new, 1)
    return text


def apply_all(root):
    p = os.path.join(root, 'index.html')
    s = open(p, encoding='utf-8').read()
    open(p, 'w', encoding='utf-8').write(apply(s, INDEX))


if __name__ == '__main__':
    apply_all(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print('patched')
