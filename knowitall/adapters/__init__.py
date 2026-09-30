"""Per-site readers for JavaScript careers sites whose JSON is too abbreviated for knowitall.json_jobs to map.

An adapter is added only after a real payload from that site has been inspected (run a scan with --debug: the log
shows the URL, keys and array sizes of every response the page loaded). It is a function

    adapter(payloads, page_url, site) -> list of jobs (make_job dicts), or None/[] when the payload is not the listing

registered under the company's registrable domain with @register("example.com"). There are none until one is needed.
"""
ADAPTERS = {}


def register(domain):
    def decorator(function):
        ADAPTERS[domain] = function
        return function
    return decorator


def for_site(site):
    """The adapter for this company, looked up by its domain and any careers domain it hands over to."""
    for domain in [site.domain, *sorted(getattr(site, "careers_domains", ()))]:
        if domain in ADAPTERS:
            return ADAPTERS[domain]
    return None
