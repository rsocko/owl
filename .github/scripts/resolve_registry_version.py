#!/usr/bin/env python3
"""Resolve OWL's next immutable GHCR semantic-version tag."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BOOTSTRAP_VERSION = (0, 2, 0)
SEMVER_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
DOCKER_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def parse_registry_repository(value: str) -> tuple[str, str]:
    normalized = str(value or "").strip().strip("/")
    if not normalized or "/" not in normalized:
        raise ValueError("registry repository must look like <registry-host>/<repository>")
    host, repository = normalized.split("/", 1)
    return host, repository


def candidate_bases(registry_host: str, authenticated: bool = False) -> list[str]:
    if registry_host.startswith(("http://", "https://")):
        base = registry_host.rstrip("/")
        if authenticated and base.startswith("http://"):
            raise ValueError("refusing to send registry credentials over plaintext HTTP")
        return [base]
    if authenticated:
        return [f"https://{registry_host}"]
    return [f"https://{registry_host}", f"http://{registry_host}"]


def build_auth_header(registry_host: str) -> dict[str, str]:
    username = str(os.environ.get("REGISTRY_USERNAME") or "").strip()
    password = str(os.environ.get("REGISTRY_PASSWORD") or "")
    if not username:
        config_path = os.path.join(os.path.expanduser("~"), ".docker", "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, encoding="utf-8") as handle:
                    auths = json.load(handle).get("auths", {})
                for key in (registry_host, f"https://{registry_host}", f"http://{registry_host}"):
                    auth_value = str(auths.get(key, {}).get("auth") or "").strip()
                    if auth_value:
                        decoded = base64.b64decode(auth_value, validate=True).decode("utf-8")
                        if ":" not in decoded:
                            raise ValueError("Docker registry credentials have an invalid format")
                        username, password = decoded.split(":", 1)
                        break
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as error:
                raise ValueError(f"could not read Docker registry credentials: {error}") from error
    if not username:
        return {}
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def exchange_bearer_token(challenge: str, headers: dict[str, str]) -> dict[str, str]:
    if not challenge.lower().startswith("bearer "):
        raise ValueError("registry returned an unsupported authentication challenge")
    parameters = dict(re.findall(r'([A-Za-z]+)="([^"]*)"', challenge[7:]))
    realm = parameters.pop("realm", "")
    if urllib.parse.urlparse(realm).scheme != "https":
        raise ValueError("registry bearer challenge must use HTTPS")
    request = urllib.request.Request(
        f"{realm}?{urllib.parse.urlencode(parameters)}",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode())
    token = str(payload.get("token") or payload.get("access_token") or "").strip()
    if not token:
        raise ValueError("registry token exchange returned no bearer token")
    return {"Authorization": f"Bearer {token}"}


def fetch_tags(base_url: str, repository: str, headers: dict[str, str]) -> list[str]:
    encoded_repo = "/".join(
        urllib.parse.quote(segment, safe="") for segment in repository.split("/")
    )
    next_url: str | None = f"{base_url}/v2/{encoded_repo}/tags/list?n=1000"
    tags: list[str] = []
    seen: set[str] = set()
    request_headers = headers
    while next_url:
        request = urllib.request.Request(next_url, headers=request_headers)
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return tags
            challenge = error.headers.get("WWW-Authenticate", "")
            if error.code != 401 or not headers or request_headers != headers or not challenge:
                raise
            request_headers = exchange_bearer_token(challenge, headers)
            continue
        with response:
            payload = json.loads(response.read().decode())
            for tag in payload.get("tags") or []:
                value = str(tag).strip()
                if value and value not in seen:
                    seen.add(value)
                    tags.append(value)
            link = response.headers.get("Link", "")
            match = re.search(r'<([^>]+)>;\s*rel="next"', link)
            next_url = urllib.parse.urljoin(next_url, match.group(1)) if match else None
    return tags


def semantic_versions(tags: list[str]) -> set[tuple[int, int, int]]:
    versions: set[tuple[int, int, int]] = set()
    for tag in tags:
        match = SEMVER_PATTERN.fullmatch(tag.strip())
        if match:
            versions.add(tuple(int(part) for part in match.groups()))
    return versions


def next_patch(tags: list[str]) -> str:
    versions = semantic_versions(tags)
    if not versions:
        candidate = BOOTSTRAP_VERSION
    else:
        highest = max(versions | {BOOTSTRAP_VERSION})
        candidate = (highest[0], highest[1], highest[2] + 1)
    while candidate in versions:
        candidate = (candidate[0], candidate[1], candidate[2] + 1)
    return ".".join(str(part) for part in candidate)


def validate_explicit_tag(tag: str) -> str:
    if not DOCKER_TAG_PATTERN.fullmatch(tag):
        raise ValueError("EXPLICIT_TAG must be a valid Docker tag")
    if not SEMVER_PATTERN.fullmatch(tag) or tag.startswith("v"):
        raise ValueError("EXPLICIT_TAG must be an unprefixed semantic version")
    return tag


def write_output(name: str, value: str) -> None:
    if output_path := os.environ.get("GITHUB_OUTPUT"):
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> int:
    repository_value = os.environ.get("REGISTRY_REPOSITORY", "")
    mode = str(os.environ.get("VERSION_MODE") or "next_patch").strip()
    explicit_tag = str(os.environ.get("EXPLICIT_TAG") or "").strip()
    if mode not in {"explicit", "next_patch"}:
        raise SystemExit(f"Unsupported VERSION_MODE: {mode}")
    if mode == "explicit":
        resolved_tag = validate_explicit_tag(explicit_tag)
    else:
        registry_host, repository = parse_registry_repository(repository_value)
        headers = build_auth_header(registry_host)
        last_error: Exception | None = None
        for base_url in candidate_bases(registry_host, authenticated=bool(headers)):
            try:
                tags = fetch_tags(base_url, repository, headers)
                resolved_tag = next_patch(tags)
                break
            except (OSError, ValueError, json.JSONDecodeError) as error:
                last_error = error
        else:
            raise SystemExit(f"Could not inspect registry tags: {last_error}")

    print(f"Resolved immutable tag: {resolved_tag}")
    write_output("resolved_tag", resolved_tag)
    write_output("resolved_image", f"{repository_value}:{resolved_tag}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        print(f"Registry HTTP error: {error.code} {body}", file=sys.stderr)
        raise
