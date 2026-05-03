"""Uninstall a module via XML-RPC.

WARNING: This is destructive. The module's data (records, custom fields,
tables) will be removed. Only run if you're sure the module is truly
unwanted.

Usage:
    python uninstall_module.py <module_name> [--target prod|staging] [--commit]
"""
import argparse
import sys
from _common import connect, make_caller


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("module")
    parser.add_argument("--target", choices=["prod", "staging"], default="staging")
    parser.add_argument("--commit", action="store_true",
                        help="actually run uninstall (default: dry-run)")
    args = parser.parse_args()

    uid, models, db, api_key = connect(args.target)
    call = make_caller(uid, models, db, api_key)

    mod = call("ir.module.module", "search_read",
               [[("name", "=", args.module)]],
               {"fields": ["id", "name", "state"]})
    if not mod:
        sys.exit(f"No module {args.module}")
    info = mod[0]
    print(f"Before: {info}")

    if info["state"] != "installed":
        print(f"Module not in 'installed' state; nothing to do.")
        return

    if not args.commit:
        print(f"DRY-RUN: would call button_immediate_uninstall on {args.module}.")
        print(f"Re-run with --commit to actually uninstall.")
        return

    print(f"Calling button_immediate_uninstall on {args.module} (id={info['id']})...")
    try:
        call("ir.module.module", "button_immediate_uninstall", [[info["id"]]])
        print("OK.")
    except Exception as e:
        print(f"Error: {str(e)[-1500:]}")
        sys.exit(1)

    info2 = call("ir.module.module", "search_read",
                 [[("id", "=", info["id"])]],
                 {"fields": ["name", "state"]})
    print(f"After: {info2}")


if __name__ == "__main__":
    main()
