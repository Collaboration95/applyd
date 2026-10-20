"""Restrict application navigation to the explicit per-run host allowlist."""
from urllib.parse import urlsplit


def allowed_hosts(policy):
    return ["linkedin.com"] + ([] if policy["easy_apply_only"] else policy["external_hosts"]["allow"])


def allowed(url, policy):
    try:
        parsed = urlsplit(url)
        parsed.port
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.encode("idna").decode().lower().rstrip(".")
        if any(c.isspace() for c in url) or "\\" in url:
            return False
    except (ValueError, TypeError, UnicodeError):
        return False
    def matches(rule):
        rule = rule.encode("idna").decode().lower().rstrip(".")
        return host == rule or host.endswith("." + rule)
    return any(matches(rule) for rule in allowed_hosts(policy)) and not any(matches(rule) for rule in policy["external_hosts"]["deny"])
