"""Entry point: `kidsmedia` / `python -m kidsmedia` starts the server."""

import logging
import os

import uvicorn

# Whose X-Forwarded-For is believed (SPEC §7): only a proxy on this host or in a private
# network, which is where the host nginx reaches the container from (Docker's bridge).
# uvicorn then takes the last address in the header that is not one of these, so a value
# the client sent itself is never used. Never "*": that takes the client's own value, and
# the login and pairing limits could be switched off with a different fake address per
# request.
DEFAULT_FORWARDED_ALLOW_IPS = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(
        "kidsmedia.main:create_app",
        factory=True,
        host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - container-internal; published on 127.0.0.1 only
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS") or DEFAULT_FORWARDED_ALLOW_IPS,
    )


if __name__ == "__main__":
    main()
