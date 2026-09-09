import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class SiemensLLMClient:
    """Reusable client for the Siemens LLM gateway.

    Credentials/config are resolved from constructor args first, falling
    back to environment variables (populated from a local .env file).
    Callers only need to supply the prompt content; the "user" role is
    fixed internally.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        resolved_api_key = api_key or os.environ.get("SS_API_KEY")
        resolved_base_url = base_url or os.environ.get("SS_API_URL")
        resolved_model = model or os.environ.get("MODEL")

        missing = [
            name
            for name, value in (
                ("SS_API_KEY", resolved_api_key),
                ("SS_API_URL", resolved_base_url),
                ("MODEL", resolved_model),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"Missing required Siemens LLM configuration: {', '.join(missing)}. "
                "Set them via constructor arguments or environment variables/.env file."
            )

        self.model = resolved_model
        self._client = OpenAI(api_key=resolved_api_key, base_url=resolved_base_url)

    def generate_llm_content(self, content: str) -> str:
        """Send `content` as a single "user" message and return the reply text."""
        completion = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": content}],
        )
        return completion.choices[0].message.content


if __name__ == "__main__":
    llm_client = SiemensLLMClient()
    result = llm_client.generate_llm_content(
        "Generate 2 negative test cases for Login scenario? Login web page will have "
        "username and password.format Title =TC1,Description=Invalid username,Expected "
        "Result=Login should fail; Title =TC2,Description=Invalid password,Expected "
        "Result=Login should fail"
    )
    print(result)