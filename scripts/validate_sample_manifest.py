"""Validate schema/real input bytes; optionally create an audited allowlist package."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replens.eval_inputs import pack_inputs, validate_manifest


def main(argv=None):
    p = argparse.ArgumentParser(description='验证真实样本元数据和模型可见材料，不认证修复成功率')
    p.add_argument('manifest')
    p.add_argument('--pack-inputs', default='', help='创建仅含允许材料的新目录（拒绝覆盖）')
    p.add_argument('--sample-id', default='')
    p.add_argument('--audit-output', default='', help='把实际文件和哈希记录到控制端 JSON')
    args = p.parse_args(argv)
    receipts, errors = validate_manifest(args.manifest)
    if errors:
        print('校验失败:')
        for error in errors:
            print('  - ' + error)
        return 1
    if args.pack_inputs and not args.sample_id:
        p.error('--pack-inputs 需要 --sample-id')
    if args.sample_id and not args.pack_inputs:
        p.error('--sample-id 需要 --pack-inputs')
    target = None
    if args.audit_output:
        target = Path(args.audit_output).resolve()
        protected = {Path(args.manifest).resolve()}
        for receipt in receipts:
            root = Path(receipt['root'])
            if target == root or root in target.parents:
                p.error('audit-output 必须放在模型输入根目录之外')
            protected.update(Path(r['path']) for r in receipt['reference_materials'])
        if target in protected:
            p.error('audit-output 不能覆盖样本 manifest 或参考材料')
    if args.pack_inputs:
        try:
            receipt = pack_inputs(args.manifest, args.sample_id, args.pack_inputs)
        except (ValueError, OSError) as exc:
            print('打包失败: ' + str(exc))
            return 1
        print('输入包已创建: ' + receipt['package_root'])
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(receipts, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS: {len(receipts)} 个样本 schema、路径隔离、实际文件清单及哈希一致；不代表行为验收通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
