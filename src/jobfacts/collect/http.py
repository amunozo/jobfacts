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
    for attempt in range(retries + 1):
        try:
            response = client.get(url)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json()
        except httpx.TransportError:
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")
