"""b2p-sync: mirror local output to R2.

远端（对象存储）后端下产物已经直接写在桶里，没有「本地 output/ → 桶」的
镜像动作可做，因此这条命令退化为 no-op（只打印一行说明），保证 start.sh
流水线里的 b2p-sync 阶段无害。
"""
from __future__ import annotations

import argparse

from bilibili_podcast.config_loader import add_backend_argument, load_active_config
from bilibili_podcast.storage import make_s3_client, sync
from bilibili_podcast.store import resolve_backend

#: Keys whose content changes every run but whose size may not: list_objects_v2
#: exposes no content hash, so they must be re-uploaded unconditionally.
_FORCE_KEYS = ("index.html",)
_FORCE_PREFIXES = ("rss",)
#: 章节文件的 Content-Type 必须是 application/json+chapters；靠后缀强制重传，
#: 让「只改了元数据」的历史对象也能被修正。
_FORCE_SUFFIXES = ("/chapters.json",)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror output/ to R2")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="output")
    add_backend_argument(parser)
    args = parser.parse_args(argv)

    config = load_active_config(args.config)
    # 只看后端类型，不去构造 store：远端模式下这条命令什么都不做，
    # 因此没必要（也不应该）因为 R2 凭据缺失而报错。
    if resolve_backend(config, args.backend) != "local":
        print(
            "sync skipped: remote backend writes artifacts directly to the bucket, "
            "no local mirror needed"
        )
        return 0

    r2 = config["R2"]
    client = make_s3_client(r2["ACCESS_KEY"], r2["SECRET_KEY"], r2["ENDPOINT_URL"])
    result = sync(
        args.output_root,
        r2["BUCKET_NAME"],
        client,
        force_prefixes=_FORCE_PREFIXES,
        force_keys=_FORCE_KEYS,
        force_suffixes=_FORCE_SUFFIXES,
    )
    print(
        f"uploaded={len(result.uploaded)} "
        f"deleted={len(result.deleted)} "
        f"skipped={len(result.skipped)} "
        f"failed={len(result.failed)}"
    )
    if result.failed:
        print("failed keys: " + ", ".join(result.failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
