#!/usr/bin/env python3
"""
Commandline-gebruik:

  python verwerk.py rapport.pdf --naam "Bisheshar" --straat Saltshof --plaats Wijchen \
      --json uit.json --pdf subsidie.pdf

Geeft JSON op stdout (of in --json) en maakt optioneel het klant-PDF.
"""
import argparse
import json
import sys
from pathlib import Path

from subsidie.parser import parse_report
from subsidie.rules import evaluate
from subsidie.report import render_pdf


def main():
    ap = argparse.ArgumentParser(description="Belisol ISDE-subsidieverwerker")
    ap.add_argument("rapport", help="Thermisch Uw-rapport (PDF) van de leverancier")
    for f in ["naam", "aanhef", "straat", "huisnummer", "postcode", "plaats", "email"]:
        ap.add_argument(f"--{f}")
    ap.add_argument("--json", help="Schrijf JSON-resultaat naar dit bestand")
    ap.add_argument("--pdf", help="Schrijf klantrapport (PDF) naar dit bestand")
    a = ap.parse_args()

    report = parse_report(a.rapport)
    klant = {k: getattr(a, k) for k in ["naam", "aanhef", "straat", "huisnummer", "postcode", "plaats", "email"] if getattr(a, k)}
    result = evaluate(report, klant)
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if a.json:
        Path(a.json).write_text(out, encoding="utf-8")
    else:
        print(out)
    if a.pdf:
        Path(a.pdf).write_bytes(render_pdf(result, report))
        print(f"PDF geschreven: {a.pdf}", file=sys.stderr)
    for w in result["waarschuwingen"]:
        print("LET OP:", w, file=sys.stderr)


if __name__ == "__main__":
    main()
