"""Force-upgrade a specific module via XML-RPC button_immediate_upgrade.

Use this if a module is stuck in 'to upgrade' state after a deployment.
Note: this is a DESTRUCTIVE operation that runs the full module upgrade
(may add columns, run migrations). Don't run on prod casually.

Usage:
    python force_module_upgrade.py <module_name> [--target prod|staging] [--commit]

Without --commit, prints what would happen but does not run the upgrade.
"""
import argparse
import sys
from _common import connect, make_caller


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("module")
    parser.add_argument("--target", choices=["prod", "staging"], default="staging")
    parser.add_argument("--commit", action="store_true", help="actually run the upgrade")
    args = parser.parse_args()

    uid, models, db, api_key = connect(args.target)
    call = make_caller(uid, models, db, api_key)

    mod = call("ir.module.module", "search_read",
               [[("name", "=", args.module)]],
               {"fields": ["id", "name", "state", "installed_version", "latest_version"]})
    if not mod:
        sys.exit(f"No module {args.module}")
    info = mod[0]
    print(f"Current: {info}")

    if not args.commit:
        print("DRY-RUN. Re-run with --commit to actually upgrade.")
        return

    print(f"Calling button_immediate_upgrade on {args.module} (id={info['id']})...")
    try:
        call("ir.module.module", "button_immediate_upgrade", [[info["id"]]])
        print("OK.")
    except Exception as e:
        msg = str(e)
        if "another module operation" in msg:
            print("Another module operation is in progress; wait and retry.")
        else:
            print(f"Error: {msg[-1500:]}")
            sys.exit(1)

    info2 = call("ir.module.module", "search_read",
                 [[("id", "=", info["id"])]],
                 {"fields": ["name", "state", "installed_version", "latest_version"]})
    print(f"After: {info2}")


if __name__ == "__main__":
    main()
