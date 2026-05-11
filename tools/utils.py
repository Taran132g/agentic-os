import json
import re

def extract_json(text: str) -> list | dict | None:
    """
    Extracts the first JSON object or array found in the text.
    Handles triple backticks and loose JSON structures.
    """
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    for ch in ("[", "{"):
        start = text.find(ch)
        if start == -1:
            continue
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
    return None
