"""Render only the reviewed gateway's fixed API address for an isolated network."""

import argparse
import ipaddress
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATES = ROOT / "tools/deploy/nginx"
UPSTREAM = "172.30.34.3:8000"


def render(
    directory: Path, subnet: str = "172.30.36.0/29", *, templates: Path = DEFAULT_TEMPLATES
) -> dict[str, str]:
    network = ipaddress.ip_network(subnet, strict=True)
    if (
        network.version != 4
        or network.prefixlen != 29
        or not network.is_private
        or network.overlaps(ipaddress.ip_network("172.30.34.0/29"))
    ):
        raise ValueError("Recovery requires a separate private IPv4 /29")
    gateway, api = str(network.network_address + 2), str(network.network_address + 3)
    files = {
        "nginx.conf": "PRL_RECOVERY_NGINX_CONF",
        "routes.conf": "PRL_RECOVERY_NGINX_ROUTES",
    }
    contents = {name: (templates / name).read_text(encoding="utf-8") for name in files}
    if any(UPSTREAM not in value for value in contents.values()):
        raise ValueError("Reviewed gateway upstream changed; inspect recovery rendering")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = {}
    for name, key in files.items():
        path = directory / name
        with path.open("x", encoding="utf-8", newline="\n") as target:
            target.write(contents[name].replace(UPSTREAM, f"{api}:8000"))
        path.chmod(0o644)  # Nonsecret files must be readable by the gateway's UID101.
        output[key] = str(path.resolve())
    return dict(
        output,
        PRL_RECOVERY_SUBNET=str(network),
        PRL_RECOVERY_GATEWAY_IP=gateway,
        PRL_RECOVERY_API_IP=api,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--subnet", default="172.30.36.0/29")
    parser.add_argument(
        "--template-directory",
        type=Path,
        default=DEFAULT_TEMPLATES,
        help="Verified retained Nginx template directory for off-host recovery",
    )
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                render(args.directory, args.subnet, templates=args.template_directory),
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError):
        print('{"operation":"recovery-config","success":false}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
