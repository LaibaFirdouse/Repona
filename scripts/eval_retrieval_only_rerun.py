#!/usr/bin/env python3
"""Re-run human questions on already-indexed repos; capture retrieval trace (LLM may fail)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from eval_human_quality import HUMAN_EVAL_REPO_TIERS, _human_questions, _run_ask  # noqa: E402
from app.db.session import SessionLocal, create_database_tables  # noqa: E402
from benchmark_suite import DEFAULT_CLIENT_ID  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tiers",
        default="small,medium",
        help="Comma-separated tier keys from HUMAN_EVAL_REPO_TIERS (default: small,medium)",
    )
    args = parser.parse_args()
    wanted = {t.strip() for t in args.tiers.split(",") if t.strip()}
    create_database_tables()
    out = {"repos": []}
    for tier, url in HUMAN_EVAL_REPO_TIERS:
        if tier not in wanted:
            continue
        row = {"tier": tier, "repo_url": url, "asks": []}
        for q in _human_questions(tier):
            db = SessionLocal()
            try:
                cold = _run_ask(db, url, DEFAULT_CLIENT_ID, q)
                cold["phase"] = "cold"
                warm = _run_ask(db, url, DEFAULT_CLIENT_ID, q)
                warm["phase"] = "warm"
            finally:
                db.close()
            row["asks"].append({"question": q, "cold": cold, "warm": warm})
        out["repos"].append(row)
    path = PROJECT_ROOT / "evaluation" / "eval-human-retrieval-rerun.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    for repo in out["repos"]:
        print(f"\n## {repo['tier']}")
        for block in repo["asks"]:
            print(block["question"][:70] + "…")
            for phase in ("cold", "warm"):
                ask = block[phase]
                ro = ask.get("retrieval_only") or {}
                print(
                    f"  {phase}: ok={ask.get('ok')} {ask.get('latency_ms')}ms "
                    f"impl_paths={ro.get('implementation_paths')}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
