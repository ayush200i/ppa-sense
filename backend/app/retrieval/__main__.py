import argparse
import json

from app.retrieval.service import create_store, ingest_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description="Index and search local contract PDFs")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("pdf")
    search = commands.add_parser("search")
    search.add_argument("question")
    search.add_argument("--doc-id")
    search.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    store = create_store()
    if args.command == "ingest":
        print(ingest_pdf(args.pdf, store).model_dump_json(indent=2))
    else:
        print(
            json.dumps(
                [
                    hit.model_dump()
                    for hit in store.search(
                        args.question,
                        doc_id=args.doc_id,
                        top_k=args.top_k,
                    )
                ],
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
