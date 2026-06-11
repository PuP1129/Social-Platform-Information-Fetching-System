from __future__ import annotations

import argparse
import json
from pathlib import Path

OUTPUT_PATH = Path("output") / "search_results.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search one keyword with Google Programmable Search Engine.")
    parser.add_argument("--keyword", required=True, help="Single keyword to search.")
    parser.add_argument("--headless", action="store_true", help="Run Chromium in headless mode.")
    parser.add_argument("--max-results", type=int, default=10, help="Maximum number of first-page results to save.")
    return parser.parse_args()


def build_output(keyword: str, results: list[dict[str, str | None]]) -> dict[str, object]:
    ranked_results = []
    for rank, result in enumerate(results, start=1):
        ranked_results.append(
            {
                "rank": rank,
                "title": result["title"],
                "url": result["url"],
                "snippet": result["snippet"],
            }
        )

    return {
        "keyword": keyword.strip(),
        "result_count": len(ranked_results),
        "results": ranked_results,
    }


def write_output(payload: dict[str, object], output_path: Path = OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    keyword = args.keyword.strip()

    try:
        from src.search.google_pse import search_google_pse
    except ModuleNotFoundError as exc:
        if exc.name == "playwright":
            raise SystemExit(
                "Playwright is not installed. Run `pip install -r requirements.txt` "
                "and then `playwright install chromium`."
            ) from exc
        raise

    print(f"Searching Google PSE for: {keyword}")
    results = search_google_pse(
        keyword=keyword,
        headless=args.headless,
        max_results=args.max_results,
    )
    payload = build_output(keyword, results)
    write_output(payload)

    print(f"Found {payload['result_count']} result(s).")
    print(f"JSON written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
