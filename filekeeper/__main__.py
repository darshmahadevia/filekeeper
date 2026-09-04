"""CLI commands keep inspection separate from explicit filesystem changes."""
import argparse
import json
import sys
from .core import SafetyError, quarantine, restore, scan


def main(argv=None):
    parser = argparse.ArgumentParser(description="Find duplicate files; quarantine and restore them.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "clean"):
        command = commands.add_parser(name)
        command.add_argument("folder")
        command.add_argument("--json", action="store_true", help="Print a machine-readable report")
        if name == "clean":
            command.add_argument("--apply", action="store_true", help="Move duplicates to quarantine")
    command = commands.add_parser("restore")
    command.add_argument("run", help="Quarantine run directory printed by clean --apply")
    args = parser.parse_args(argv)
    try:
        if args.command == "restore":
            print(f"Restored {restore(args.run)} file(s).")
            return 0
        report = scan(args.folder)
        if args.command == "clean" and args.apply:
            run = quarantine(report)
            report["quarantine_run"] = str(run) if run else None
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Scanned {report['files_scanned']} files; hashed {report['files_hashed']} candidates.")
            print(f"Found {len(report['groups'])} duplicate groups, {report['duplicate_bytes']:,} duplicate bytes.")
            for group in report["groups"]:
                print(f"\nKeep: {group['keep']!r}")
                for name in group["duplicates"]:
                    print(f"  Duplicate: {name!r}")
            for error in report["errors"]:
                print(f"Scan error: {error!r}", file=sys.stderr)
            if report.get("quarantine_run"):
                print(f"\nQuarantine: {report['quarantine_run']}")
                print("Restore with: python -m filekeeper restore <quarantine-directory>")
            elif args.command == "clean" and not args.apply:
                print("\nPreview only. Add --apply to quarantine duplicates.")
        return 1 if report["errors"] else 0
    except (OSError, SafetyError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {str(exc)!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
