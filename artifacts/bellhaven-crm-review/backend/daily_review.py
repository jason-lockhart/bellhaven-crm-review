"""Read-only daily reconciliation entry point for the inert scheduling example."""

from . import pipeline, store


def main() -> None:
    store.initialize()
    result = pipeline.run_reconciliation()
    print(
        "Bellhaven read-only run: "
        f"{result['communities_scanned']} communities, "
        f"{result['accounts_scanned']} CRM accounts, "
        f"{result['proposals_created']} new proposals."
    )
    for warning in result["warnings"]:
        print(f"Warning: {warning}")


if __name__ == "__main__":
    main()
