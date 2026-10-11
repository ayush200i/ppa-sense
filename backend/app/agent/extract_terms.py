import argparse

from app.agent.service import create_terms_pipeline
from app.config import Settings
from app.schemas.terms import ExtractionRequest


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract commercial terms with evidence")
    parser.add_argument("doc_id")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()
    result = create_terms_pipeline(Settings()).extract(
        ExtractionRequest(
            doc_id=args.doc_id,
            top_k=args.top_k,
            max_retries=args.max_retries,
        )
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
