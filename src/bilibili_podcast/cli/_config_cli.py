import io
import sys
from pathlib import Path

import yaml

from bilibili_podcast.extract_url import parse_sources, to_legacy_config


EXIT_OK = 0
EXIT_GENERAL = 1
EXIT_FORMAT = 2
EXIT_TYPE = 3
EXIT_CONFLICT = 4


def main(argv: list[str], stdout: io.StringIO | None = None, stderr: io.StringIO | None = None) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    config_path = Path(argv[0])
    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    sources = config.get("sources")
    has_legacy = "season" in config or "series" in config

    if sources is not None and has_legacy:
        print(
            "error: config defines both 'sources' and 'season'/'series'; use one or the other",
            file=stderr,
        )
        return EXIT_CONFLICT

    if sources is not None:
        try:
            parsed = parse_sources(sources)
        except ValueError as e:
            print(f"error: {e}", file=stderr)
            return EXIT_GENERAL
        legacy = to_legacy_config(parsed)
        config["season"] = legacy["season"]
        config["series"] = legacy["series"]

    yaml.safe_dump(config, stdout, allow_unicode=True, sort_keys=False)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
