"""Check the actual resolved Compose boundary without contacting a Docker engine."""

import json
import subprocess


def main() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "test",
            "--profile",
            "tools",
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(result.stdout)
    services = config["services"]
    assert config["networks"]["backend"]["internal"] is True
    assert set(services["worker"]["networks"]) == {"backend"}, (
        "Document extraction must stay on the private backend network"
    )
    for name in ("postgres", "redis", "worker", "migrate", "secret-init"):
        assert not services[name].get("ports"), f"{name} must not publish a host port"
    for name in ("api", "worker"):
        assert services[name]["environment"]["DATABASE_USER"] == "aka_app"
        mounts = {volume["source"] for volume in services[name]["volumes"]}
        assert mounts == {"app-secrets"}, f"{name} must not receive owner/Redis-server secrets"
        assert all(volume["read_only"] for volume in services[name]["volumes"])
    for name in ("api", "web"):
        assert all(port["host_ip"] == "127.0.0.1" for port in services[name]["ports"])
    assert services["migrate"]["environment"]["DATABASE_USER"] == "aka_owner"
    assert services["postgres"]["volumes"][0]["target"] == "/var/lib/postgresql"
    assert not any(volume.get("external") for volume in config["volumes"].values())
    print("Resolved Compose config has private data services and separated runtime credentials.")


if __name__ == "__main__":
    main()
