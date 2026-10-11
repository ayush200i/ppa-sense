import argparse

from app.agent.service import create_answer_pipeline
from app.config import Settings
from app.schemas.answer import AnswerRequest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Answer a contract question with checked citations"
    )
    parser.add_argument("question")
    parser.add_argument("--doc-id")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rerank", action="store_true")
    args = parser.parse_args()
    result = create_answer_pipeline(Settings()).answer(
        AnswerRequest(
            question=args.question,
            doc_id=args.doc_id,
            top_k=args.top_k,
            rerank=args.rerank,
        )
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
