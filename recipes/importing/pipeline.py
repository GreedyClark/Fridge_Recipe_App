from ..models import RecipeImport
from .errors import ImportFailed
from .extract import PAGE_TEXT_LIMIT, RawRecipe, extract_recipe
from .fetch import fetch_page
from .gemini import parse_recipe


def fail(record, exc):
    record.status = RecipeImport.FAILED
    record.error = str(exc)
    record.save(update_fields=["status", "error"])


def run_import(record, session=None, client=None):
    try:
        page = fetch_page(record.url, session=session)
        raw = extract_recipe(page.content, page.url, page.encoding)
        record.data = parse_recipe(raw, client=client, user=record.user)
    except ImportFailed as exc:
        fail(record, exc)
        raise
    record.save(update_fields=["data"])
    return record


def run_text_import(record, text, client=None):
    raw = RawRecipe(url="", source="pasted", page_text=text.strip()[:PAGE_TEXT_LIMIT])
    try:
        record.data = parse_recipe(raw, client=client, user=record.user)
    except ImportFailed as exc:
        fail(record, exc)
        raise
    record.save(update_fields=["data"])
    return record
