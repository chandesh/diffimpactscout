"""Sample the endpoint chain's effective false-positive rate on any target repo.

Usage: python scripts/sample_effective_fp.py <repo-root> [--sample N]

Runs chain E's extraction against the repo, samples up to N rows, and
prints each sampled row for manual true/false-positive classification plus
the resulting effective-FP rate. Ships with no bundled corpus.
"""

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import diffimpactscout.config as config  # noqa: E402
from diffimpactscout.impact import reverse as rev  # noqa: E402
from diffimpactscout.impact.diff_parser import get_file_changes  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--sample", type=int, default=30)
    args = parser.parse_args(argv)
    cfg = config.load_config(args.root)
    changes = get_file_changes(args.root, None, False, None, None)
    js_changes = [c for c in changes if c.ext in ("js", "ts", "jsx", "tsx")]
    tracked = set(rev._tracked_files(args.root, cfg, "*.ts", "*.tsx", "*.js", "*.jsx"))
    refs = rev._http_refs_for_files(args.root, [c.path for c in js_changes], tracked)
    print(json.dumps({"files": len(js_changes), "refs": len(refs)}, indent=2))
    sample = random.Random(42).sample(refs, min(args.sample, len(refs))) if refs else []
    for ref in sample:
        print("%s:%s  %s %s" % (ref["file"], ref["line"], ref.get("method") or "get", ref["ref"]))
    if sample:
        print(
            "\nClassify each sampled ref as TRUE/FALSE positive, then compute:\n"
            "effective FP rate = false / sample (Tricorder bar: < 0.10)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
