"""独立评测入口：python eval/eval.py <repo> --dataset eval/datasets/dataset.json"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from replens.eval_runner import run_eval  # noqa: E402


def main():
    import argparse
    p = argparse.ArgumentParser(description="RepoLens-OSS 评测")
    p.add_argument("repo", help="目标仓库路径")
    p.add_argument("--dataset", required=True, help="评测集 JSON")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--hybrid", action="store_true")
    p.add_argument('--candidate-limit', type=int, default=100)
    p.add_argument('--input-manifest', default='')
    p.add_argument('--sample-id', default='')
    args = p.parse_args()
    try:
        run_eval(args.repo, args.dataset, top_k=args.top_k, hybrid=args.hybrid,
                 candidate_limit=args.candidate_limit, input_manifest=args.input_manifest, sample_id=args.sample_id)
    except (ValueError, OSError) as exc:
        p.error(str(exc))


if __name__ == "__main__":
    main()
