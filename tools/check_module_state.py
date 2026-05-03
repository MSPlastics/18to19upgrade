"""Check the state of a module on prod or staging.

Usage:
    python check_module_state.py <module_name> [--target prod|staging]
"""
import argparse
import sys
from _common import connect, make_caller


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("module", help="Module technical name (e.g. product_customerinfo)")
    parser.add_argument("--target", choices=["prod", "staging"], default="prod")
    args = parser.parse_args()

    uid, models, db, api_key = connect(args.target)
    call = make_caller(uid, models, db, api_key)

    mods = call("ir.module.module", "search_read",
                [[("name", "=", args.module)]],
                {"fields": ["id", "name", "state", "installed_version", "latest_version", "shortdesc"]})
    if not mods:
        print(f"No module named '{args.module}' on {args.target}")
        sys.exit(1)
    for m in mods:
        print(m)


if __name__ == "__main__":
    main()
