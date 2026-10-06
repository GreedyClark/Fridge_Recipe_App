import html
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

PAGE_TEXT_LIMIT = 15000
NOISE_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]
TAG_RE = re.compile(r"<[^>]+>")
SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([.,;:!?])")


@dataclass
class RawRecipe:
    url: str
    source: str
    title: str = ""
    image_url: str = ""
    ingredients: list = field(default_factory=list)
    instructions: list = field(default_factory=list)
    yield_text: str = ""
    page_text: str = ""


def clean(value):
    if value is None:
        return ""
    text = " ".join(html.unescape(TAG_RE.sub(" ", str(value))).split())
    return SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", text)


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def walk(node):
    if isinstance(node, list):
        for item in node:
            yield from walk(item)
    elif isinstance(node, dict):
        yield node
        if "@graph" in node:
            yield from walk(node["@graph"])


def is_recipe(node):
    return "Recipe" in as_list(node.get("@type"))


def find_jsonld_recipe(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except ValueError:
            continue
        for node in walk(data):
            if is_recipe(node):
                return node
    return None


def image_from(value):
    for item in as_list(value):
        if isinstance(item, str) and item:
            return item
        if isinstance(item, dict) and item.get("url"):
            return item["url"]
    return ""


def instructions_from(value):
    steps = []
    for item in as_list(value):
        if isinstance(item, str):
            steps.extend(clean(line) for line in item.splitlines())
        elif isinstance(item, dict):
            if "itemListElement" in item:
                steps.extend(instructions_from(item["itemListElement"]))
            else:
                steps.append(clean(item.get("text") or item.get("name")))
    return [step for step in steps if step]


def meta_content(soup, prop):
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return clean(tag.get("content")) if tag else ""


def page_text(soup):
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    lines = [" ".join(line.split()) for line in root.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)[:PAGE_TEXT_LIMIT]


def extract_recipe(content, url, encoding=None):
    if isinstance(content, bytes):
        soup = BeautifulSoup(content, "html.parser", from_encoding=encoding)
    else:
        soup = BeautifulSoup(content, "html.parser")

    og_title = meta_content(soup, "og:title")
    og_image = meta_content(soup, "og:image")
    recipe = find_jsonld_recipe(soup)

    if recipe:
        image = image_from(recipe.get("image")) or og_image
        return RawRecipe(
            url=url,
            source="jsonld",
            title=clean(recipe.get("name")) or og_title,
            image_url=urljoin(url, image) if image else "",
            ingredients=[clean(item) for item in as_list(recipe.get("recipeIngredient")) if clean(item)],
            instructions=instructions_from(recipe.get("recipeInstructions")),
            yield_text=clean(next(iter(as_list(recipe.get("recipeYield"))), "")),
        )

    heading = soup.find("h1")
    title = (clean(heading.get_text()) if heading else "") or og_title or clean(soup.title.string if soup.title else "")
    return RawRecipe(
        url=url,
        source="text",
        title=title,
        image_url=urljoin(url, og_image) if og_image else "",
        page_text=page_text(soup),
    )