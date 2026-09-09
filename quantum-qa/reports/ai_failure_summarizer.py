"""Generates an AI-authored overall recommendation for workflow failures."""

from llm.client import SiemensLLMClient


def summarize_failures(execution_data: dict, failures: list[dict]) -> str | None:
    """Return one consolidated recommendation paragraph, or None if there are no failures."""
    if not failures:
        return None

    prompt = _build_prompt(execution_data, failures)
    try:
        llm_client = SiemensLLMClient()
        return llm_client.generate_llm_content(prompt)
    except Exception:
        return (
            f"AI summary unavailable. {len(failures)} step(s) failed during this run; "
            "review the failed steps below for details."
        )


def _build_prompt(execution_data: dict, failures: list[dict]) -> str:
    title = execution_data.get("title", "")
    final_url = execution_data.get("final_url", "")
    failure_lines = "\n".join(
        f"- Stage {failure.get('stage')}: {failure.get('raw_step', '')} -> {failure.get('error', '')}"
        for failure in failures
    )
    return (
        "You are a QA lead reviewing an automated UI test run.\n"
        f"Page: {title}\n"
        f"Final URL: {final_url}\n"
        f"Total failures: {len(failures)}\n\n"
        "Failed steps:\n"
        f"{failure_lines}\n\n"
        "Write one concise overall recommendation paragraph covering: the total failure count, "
        "which failures are most important/blocking to the business, and an overall go/no-go "
        "recommendation. Do not list every failure individually; summarize."
    )
