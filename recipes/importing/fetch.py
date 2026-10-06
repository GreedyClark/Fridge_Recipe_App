import ipaddress
import socket
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests

from .errors import ImportFailed

USER_AGENT = "FridgeRecipeBot/1.0 (+https://github.com/GreedyClark/Fridge_Recipe_App)"
TIMEOUT = 10
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3


@dataclass
class FetchedPage:
    url: str
    content: bytes
    encoding: str | None = None


def ensure_public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ImportFailed("Потрібне посилання, що починається з http:// або https://")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ImportFailed("Не вдалося знайти такий сайт. Перевір посилання.") from exc

    for address in addresses:
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ImportFailed("Посилання веде на внутрішню адресу, такі сторінки імпортувати не можна.")


def allowed_by_robots(session, url):
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = session.get(robots_url, timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException:
        return True

    if response.status_code in (401, 403):
        return False
    if response.status_code != 200:
        return True

    parser = robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def read_limited(response):
    chunks = []
    size = 0
    for chunk in response.iter_content(chunk_size=65536):
        size += len(chunk)
        if size > MAX_BYTES:
            raise ImportFailed("Сторінка завелика для імпорту.")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_page(url, session=None):
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    ensure_public_url(url)
    if not allowed_by_robots(session, url):
        raise ImportFailed("Сайт забороняє автоматичне завантаження сторінок.")

    current = url
    for _ in range(MAX_REDIRECTS + 1):
        ensure_public_url(current)
        try:
            response = session.get(current, timeout=TIMEOUT, allow_redirects=False, stream=True)
        except requests.Timeout as exc:
            raise ImportFailed("Сайт не відповідає. Спробуй пізніше.") from exc
        except requests.RequestException as exc:
            raise ImportFailed("Не вдалося відкрити сторінку.") from exc

        if not response.is_redirect:
            break
        location = response.headers.get("Location", "")
        response.close()
        if not location:
            raise ImportFailed("Сайт повернув некоректну переадресацію.")
        current = urljoin(current, location)
    else:
        raise ImportFailed("Забагато переадресацій.")

    if response.status_code != 200:
        raise ImportFailed(f"Сайт повернув помилку {response.status_code}.")

    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type:
        raise ImportFailed("За посиланням не веб-сторінка.")

    encoding = response.encoding if "charset=" in content_type else None
    return FetchedPage(url=current, content=read_limited(response), encoding=encoding)