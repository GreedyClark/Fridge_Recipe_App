from ..models import RecipeImport
from .errors import ImportFailed
from .extract import extract_recipe
from .fetch import fetch_page
from .gemini import parse_recipe


def run_import(record, session=None, client=None):
    try:
        page = fetch_page(record.url, session=session)
        raw = extract_recipe(page.content, page.url, page.encoding)
        record.data = parse_recipe(raw, client=client)
    except ImportFailed as exc:
        record.status = RecipeImport.FAILED
        record.error = str(exc)
        record.save(update_fields=["status", "error"])
        raise
    record.save(update_fields=["data"])
    return record