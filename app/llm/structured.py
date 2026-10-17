import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.errors import LLMOutputError
from app.llm.client import LLMClient

PROMPT_VERSION = "screening.v1"
PROMPT_PATH = Path(__file__).parent / "prompts/screening/screening.v1.md"


def structured_call[T: BaseModel](
    client: LLMClient, schema: type[T], description: str, *, max_tokens: int
) -> tuple[T, int, int]:
    system = PROMPT_PATH.read_text(encoding="utf-8")
    # Quotes, JSON escapes, and explicit boundaries keep external content in a data field.
    user = "<document>" + json.dumps(description, ensure_ascii=False) + "</document>"
    system += "\nExpected JSON schema: " + json.dumps(schema.model_json_schema())
    tokens_in = tokens_out = 0
    for attempt in range(2):
        result = client.complete(
            system, user, schema=schema.model_json_schema(), temperature=0, max_tokens=max_tokens
        )
        tokens_in += result.tokens_in
        tokens_out += result.tokens_out
        try:
            return schema.model_validate_json(result.text), tokens_in, tokens_out
        except ValidationError as error:
            # Validation errors must not echo the source, completions or provider secrets.
            issues = [
                {"loc": item["loc"], "type": item["type"]}
                for item in error.errors(include_input=False, include_url=False)
            ]
            if attempt == 0:
                user += "\nRepair the JSON output. Validation issues: " + json.dumps(issues)
    raise LLMOutputError("Réponse IA invalide après une tentative de réparation.")
