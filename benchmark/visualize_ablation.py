#!/usr/bin/env python3
"""
Render a lightweight HTML dashboard from an ablation JSON bundle.

No extra dependencies, no reruns, no additional token spend.
"""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path


RESULTS_DIR = Path(__file__).parent / "results" / "ablation"


def latest_bundle() -> Path:
    files = sorted(RESULTS_DIR.glob("ablation_*.json"))
    if not files:
        raise FileNotFoundError("No ablation JSON found.")
    return files[-1]


def runtime_seconds(row: dict) -> float:
    total = 0.0
    for item in row.get("per_audit", []):
        total += float(
            item.get("elapsed_seconds", item.get("total_elapsed", 0.0)) or 0.0
        )
    return total


def _scale(value: float, lo: float, hi: float, out_lo: float, out_hi: float) -> float:
    if hi <= lo:
        return (out_lo + out_hi) / 2
    ratio = (value - lo) / (hi - lo)
    return out_lo + ratio * (out_hi - out_lo)


def scatter_svg(rows: list[dict]) -> str:
    width = 760
    height = 360
    left = 70
    right = 20
    top = 20
    bottom = 50
    inner_w = width - left - right
    inner_h = height - top - bottom

    costs = [float(r.get("total_cost_usd", 0.0) or 0.0) for r in rows]
    recalls = [float(r.get("overall_recall", 0.0) or 0.0) for r in rows]
    x_min = 0.0
    x_max = max(costs) * 1.1 if costs else 1.0
    y_min = 0.0
    y_max = max(max(recalls) * 1.1, 1.0 if any(recalls) else 1.0)

    palette = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#17becf"]

    circles = []
    labels = []
    for idx, row in enumerate(rows):
        x = left + _scale(float(row.get("total_cost_usd", 0.0) or 0.0), x_min, x_max, 0, inner_w)
        y = top + inner_h - _scale(float(row.get("overall_recall", 0.0) or 0.0), y_min, y_max, 0, inner_h)
        color = palette[idx % len(palette)]
        label = escape(row["profile"])
        circles.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='6' fill='{color}' />")
        labels.append(f"<text x='{x + 8:.1f}' y='{y - 8:.1f}' font-size='12' fill='{color}'>{label}</text>")

    x_ticks = []
    for i in range(5):
        value = x_min + (x_max - x_min) * i / 4
        x = left + inner_w * i / 4
        x_ticks.append(f"<line x1='{x:.1f}' y1='{top+inner_h}' x2='{x:.1f}' y2='{top+inner_h+6}' stroke='#666' />")
        x_ticks.append(f"<text x='{x:.1f}' y='{top+inner_h+22}' text-anchor='middle' font-size='11'>${value:.2f}</text>")

    y_ticks = []
    for i in range(5):
        value = y_min + (y_max - y_min) * i / 4
        y = top + inner_h - inner_h * i / 4
        y_ticks.append(f"<line x1='{left-6}' y1='{y:.1f}' x2='{left}' y2='{y:.1f}' stroke='#666' />")
        y_ticks.append(f"<text x='{left-12}' y='{y+4:.1f}' text-anchor='end' font-size='11'>{value:.0%}</text>")

    return f"""
<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <rect x="0" y="0" width="{width}" height="{height}" fill="white"/>
  <line x1="{left}" y1="{top+inner_h}" x2="{left+inner_w}" y2="{top+inner_h}" stroke="#222"/>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{top+inner_h}" stroke="#222"/>
  {''.join(x_ticks)}
  {''.join(y_ticks)}
  {''.join(circles)}
  {''.join(labels)}
  <text x="{left + inner_w/2:.1f}" y="{height-10}" text-anchor="middle" font-size="12">Total Cost (USD)</text>
  <text x="18" y="{top + inner_h/2:.1f}" text-anchor="middle" font-size="12" transform="rotate(-90 18 {top + inner_h/2:.1f})">Recall</text>
  <text x="{width/2:.1f}" y="14" text-anchor="middle" font-size="14" font-weight="bold">Cost vs Recall</text>
</svg>
""".strip()


def bars_svg(rows: list[dict]) -> str:
    width = 760
    height = 360
    left = 70
    right = 20
    top = 20
    bottom = 90
    inner_w = width - left - right
    inner_h = height - top - bottom

    values = [float(r.get("cost_per_detected", 0.0) or 0.0) for r in rows]
    y_max = max(values) * 1.1 if values else 1.0
    bar_w = inner_w / max(len(rows), 1) * 0.65
    gap = inner_w / max(len(rows), 1)
    palette = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#17becf"]

    bars = []
    for idx, row in enumerate(rows):
        value = float(row.get("cost_per_detected", 0.0) or 0.0)
        x = left + gap * idx + (gap - bar_w) / 2
        h = _scale(value, 0, y_max, 0, inner_h)
        y = top + inner_h - h
        color = palette[idx % len(palette)]
        bars.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_w:.1f}' height='{h:.1f}' fill='{color}' />")
        bars.append(f"<text x='{x + bar_w/2:.1f}' y='{y-6:.1f}' text-anchor='middle' font-size='11'>${value:.2f}</text>")
        bars.append(
            f"<text x='{x + bar_w/2:.1f}' y='{top+inner_h+18:.1f}' text-anchor='middle' font-size='11' transform='rotate(25 {x + bar_w/2:.1f} {top+inner_h+18:.1f})'>{escape(row['profile'])}</text>"
        )

    y_ticks = []
    for i in range(5):
        value = y_max * i / 4
        y = top + inner_h - inner_h * i / 4
        y_ticks.append(f"<line x1='{left-6}' y1='{y:.1f}' x2='{left}' y2='{y:.1f}' stroke='#666' />")
        y_ticks.append(f"<text x='{left-12}' y='{y+4:.1f}' text-anchor='end' font-size='11'>${value:.2f}</text>")

    return f"""
<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <rect x="0" y="0" width="{width}" height="{height}" fill="white"/>
  <line x1="{left}" y1="{top+inner_h}" x2="{left+inner_w}" y2="{top+inner_h}" stroke="#222"/>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{top+inner_h}" stroke="#222"/>
  {''.join(y_ticks)}
  {''.join(bars)}
  <text x="18" y="{top + inner_h/2:.1f}" text-anchor="middle" font-size="12" transform="rotate(-90 18 {top + inner_h/2:.1f})">Cost Per Detect</text>
  <text x="{width/2:.1f}" y="14" text-anchor="middle" font-size="14" font-weight="bold">Cost Efficiency by Profile</text>
</svg>
""".strip()


def table_html(rows: list[dict]) -> str:
    header = """
<table>
  <thead>
    <tr>
      <th>Profile</th>
      <th>Runner</th>
      <th>Recall</th>
      <th>Total Cost</th>
      <th>$/Detect</th>
      <th>Detect/$</th>
      <th>Runtime</th>
    </tr>
  </thead>
  <tbody>
""".strip()
    body = []
    for row in sorted(rows, key=lambda item: (-item.get("overall_recall", 0.0), item.get("cost_per_detected", 0.0))):
        body.append(
            "<tr>"
            f"<td>{escape(row['profile'])}</td>"
            f"<td>{escape(row.get('runner', ''))}</td>"
            f"<td>{row.get('overall_recall', 0.0):.1%}</td>"
            f"<td>${row.get('total_cost_usd', 0.0):.4f}</td>"
            f"<td>${row.get('cost_per_detected', 0.0):.4f}</td>"
            f"<td>{row.get('detected_per_dollar', 0.0):.4f}</td>"
            f"<td>{runtime_seconds(row):.1f}s</td>"
            "</tr>"
        )
    return header + "\n" + "\n".join(body) + "\n</tbody>\n</table>"


def render(bundle: dict, source_path: Path) -> str:
    rows = bundle.get("results", [])
    audits = ", ".join(bundle.get("audits", []))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>EVMBench Ablation Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #111; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .meta {{ color: #444; margin-bottom: 18px; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 22px; }}
    .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 16px; background: #fff; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #eee; padding: 8px 10px; text-align: left; }}
    th {{ background: #fafafa; }}
    code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 6px; }}
  </style>
</head>
<body>
  <h1>EVMBench Ablation Dashboard</h1>
  <div class="meta">
    Source: <code>{escape(str(source_path))}</code><br/>
    Timestamp: {escape(str(bundle.get("timestamp", "")))}<br/>
    Audits: {escape(audits)}
  </div>
  <div class="grid">
    <div class="card">
      <h2>Summary</h2>
      {table_html(rows)}
    </div>
    <div class="card">
      <h2>Frontier</h2>
      {scatter_svg(rows)}
    </div>
    <div class="card">
      <h2>Cost Per Detect</h2>
      {bars_svg(rows)}
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize EVMBench ablation results")
    parser.add_argument("--input", type=str, default=None, help="Ablation JSON path")
    parser.add_argument("--output", type=str, default=None, help="Output HTML path")
    args = parser.parse_args()

    input_path = Path(args.input) if args.input else latest_bundle()
    with open(input_path) as f:
        bundle = json.load(f)

    output_path = Path(args.output) if args.output else input_path.with_suffix(".html")
    output_path.write_text(render(bundle, input_path), encoding="utf-8")
    print(f"Saved dashboard to {output_path}")


if __name__ == "__main__":
    main()
