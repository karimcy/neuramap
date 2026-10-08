#!/usr/bin/env python3
"""Precompute industrial-site counts per anchor node (for the results page score)."""
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
counts = {}
for f in glob.glob(os.path.join(ROOT, 'sites', '*.geojson')):
    cc = os.path.basename(f).split('.')[0]
    gj = json.load(open(f))
    for ft in gj.get('features', []):
        node = ft['properties'].get('node')
        if node:
            key = cc + '|' + node
            counts[key] = counts.get(key, 0) + 1
out = os.path.join(ROOT, 'data', 'site_counts.json')
json.dump(counts, open(out, 'w'))
print(f'{len(counts)} node keys → {out} ({os.path.getsize(out)//1024} KB)')
