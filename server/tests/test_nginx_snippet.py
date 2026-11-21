"""The shipped nginx snippet (deploy/nginx/kidsmedia.conf, SPEC §4.1) names the app's address
once. A port written into a nested upload location was missed when KM_PORT changed, and the
tablet's photos went to another service on 8080."""

import re
from pathlib import Path

SNIPPET = Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "kidsmedia.conf"


def directives(name: str) -> list[str]:
    lines = [line.split("#", 1)[0].strip() for line in SNIPPET.read_text().splitlines()]
    return [line for line in lines if re.match(rf"{re.escape(name)}\s", line)]


def test_every_proxy_pass_uses_the_one_backend_variable() -> None:
    passes = directives("proxy_pass")
    assert len(passes) >= 5  # location / and the upload locations
    assert all(p == "proxy_pass $km_backend;" for p in passes), passes


def test_the_port_is_set_in_one_place() -> None:
    assert directives("set $km_backend") == ["set $km_backend http://127.0.0.1:8080;"]
    body = "\n".join(line.split("#", 1)[0] for line in SNIPPET.read_text().splitlines())
    assert body.count("127.0.0.1:") == 1
