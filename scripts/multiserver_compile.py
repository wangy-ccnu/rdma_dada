#!/usr/bin/env python3
"""Local multi-server bundle generation and explicit compiler resolution."""

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.multiserver.bundle import GENERATOR_VERSION, build_bundle
from scripts.multiserver.compiler import resolve_instance


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("CLI_ARGUMENT: " + message)


def main(argv=None):
    parser = _Parser(description="Build portable multi-server instance bundles")
    parser.add_argument("--version", action="version", version=GENERATOR_VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)
    compile_parser = subparsers.add_parser("compile")
    for option in ("global", "deployments", "inventory", "output"):
        compile_parser.add_argument("--" + option, required=True, type=Path)
    resolve_parser = subparsers.add_parser("resolve")
    for option in ("instance-dir", "compiler", "output"):
        resolve_parser.add_argument("--" + option, required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        if args.command == "compile":
            result = build_bundle(getattr(args, "global"), args.deployments, args.output,
                                  inventory_path=args.inventory)
        else:
            result = resolve_instance(args.instance_dir, args.compiler, args.output)
    except ValueError as error:
        message = str(error)
        failure = {"ok": False, "error": {"code": message.split(":", 1)[0], "message": message}}
        if hasattr(error, "details"):
            failure["compiler_failure"] = error.details
        print(json.dumps(failure, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
