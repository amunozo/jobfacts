"""Shared HTTP client settings and a small retrying GET."""

import time

import httpx

USER_AGENT = "jobfacts/0.1 (personal research project; polite daily collector)"


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(20.0),
        follow_redirects=True,
    )


def get_json(client: httpx.Client, url: str, retries: int = 2):
    """GET a JSON document; retry on 429/5xx and network errors. Raises on final failure."""
    return _get(client, url, retries).json()


def get_text(client: httpx.Client, url: str, retries: int = 2) -> str:
    """GET a text document (for example an XML feed), with the same retries as `get_json`."""
    return _get(client, url, retries, accept="application/xml, application/rss+xml, text/xml").text


def _get(client: httpx.Client, url: str, retries: int, accept: str | None = None):
    headers = {"Accept": accept} if accept else None
    for attempt in range(retries + 1):
        try:
            response = client.get(url, headers=headers)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            response.raise_for_status()
            return response
        except httpx.TransportError:
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")
