"""Shared HTTP session. Yahoo and NSE both reject default/library user agents."""
import logging
import time

import requests

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_session = None


def session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})
        adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
        _session.mount("https://", adapter)
    return _session


def get(url, *, timeout=30, retries=3, ok404=False, **kw):
    """GET with backoff on throttling and transient errors. Returns the Response, or None on 404 if ok404."""
    err = None
    for attempt in range(retries + 1):
        try:
            r = session().get(url, timeout=timeout, **kw)
            if r.status_code == 404 and ok404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                err = requests.HTTPError(f"{r.status_code} for {url}")
            else:
                r.raise_for_status()
                return r
        except (requests.ConnectionError, requests.Timeout) as e:
            err = e
        if attempt < retries:
            time.sleep(1.5 * 2 ** attempt)
    raise err
