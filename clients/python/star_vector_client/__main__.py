import argparse
import json
from pathlib import Path
import sys

from . import DocumentStore, SpacyEncoder


def read_input(path):
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Local pinned Star Vector document embeddings")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("embedding-profile")
    upsert = commands.add_parser("embed-upsert")
    upsert.add_argument("store")
    upsert.add_argument("document", help="canonical JSON file, or - for stdin")
    search = commands.add_parser("embed-search")
    search.add_argument("store")
    search.add_argument("metric", choices=["cosine", "dot", "euclidean", "manhattan"])
    search.add_argument("limit", type=int)
    search.add_argument("query", help="UTF-8 query file, or - for stdin")
    args = parser.parse_args()
    if args.command == "embedding-profile":
        result = SpacyEncoder().profile
    else:
        # Read/parse the request before opening a store. No document/text is a shell argument.
        payload = read_input(args.document if args.command == "embed-upsert" else args.query)
        if args.command == "embed-upsert":
            payload = json.loads(payload)
        elif args.limit < 0:
            raise ValueError("limit must be non-negative")
        with DocumentStore(args.store) as store:
            if args.command == "embed-upsert":
                document = store.upsert(payload)
                result = {"upserted": True, "id": document["id"]}
            else:
                result = store.search(payload, args.metric, args.limit)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


def cli():
    try:
        main()
    except Exception as error:
        print("star-vector: " + str(error), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli()
