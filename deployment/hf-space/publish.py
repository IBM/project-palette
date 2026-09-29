#!/usr/bin/env python3
"""Create or update the Palette Hugging Face Space from this folder.

    python publish.py ibm-research/palette-agent --dry-run   # read-only report
    python publish.py ibm-research/palette-agent             # create (private) / add new files
    python publish.py ibm-research/palette-agent --yes       # redeploy: replace our files

Needs only a Hugging Face login (`hf auth login`, or HF_TOKEN in the
environment). No .env file is read.

Safety:
  * Never deletes anything — no files, variables, secrets, or the Space itself.
  * Uploads exactly SPACE_FILES (an allowlist); nothing else in this folder.
  * Replacing an existing file or setting in the Space requires --yes; the
    dry run lists every such change first. Old file versions stay in the
    Space's git history.
  * Space settings change only through explicit flags (--upstream, --auth),
    never from whatever happens to be exported in your shell.

Full guide: DEPLOY.md.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from huggingface_hub import HfApi

HERE = Path(__file__).resolve().parent
# Exactly what the Space needs — an allowlist, so a local .venv, .env or any
# other stray file in this folder can never be uploaded.
SPACE_FILES = ["README.md", "DEPLOY.md", "Dockerfile", "entrypoint.sh",
               "nginx.conf.template", "publish.py", ".gitignore"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo_id", help="<org-or-user>/<space-name>, e.g. ibm-research/palette-agent")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what exists and what would change; change nothing")
    ap.add_argument("--yes", action="store_true",
                    help="allow replacing existing files/settings in the Space")
    ap.add_argument("--public", action="store_true",
                    help="when CREATING the Space, make it public (default: private). "
                         "Never changes an existing Space's visibility.")
    ap.add_argument("--upstream", metavar="URL",
                    help="set the Space variable UPSTREAM_URL (the palette URL to forward to)")
    ap.add_argument("--auth", action="store_true",
                    help="set the PROXY_USER / PROXY_PASSWORD secrets from those "
                         "environment variables (turns on the password prompt)")
    args = ap.parse_args()

    missing = [f for f in SPACE_FILES if not (HERE / f).is_file()]
    if missing:
        raise SystemExit(f"missing Space files in {HERE}: {missing}")
    auth = None
    if args.auth:
        auth = (os.environ.get("PROXY_USER"), os.environ.get("PROXY_PASSWORD"))
        if not all(auth):
            raise SystemExit("--auth needs PROXY_USER and PROXY_PASSWORD in the environment")

    api = HfApi(token=os.environ.get("HF_TOKEN"))
    print(f"logged in as: {api.whoami()['name']}")
    rid = args.repo_id
    replacing: list[str] = []   # existing things this run would replace

    # A model or dataset repo with the same name is a separate repo: untouched.
    for other in ("model", "dataset"):
        if api.repo_exists(rid, repo_type=other):
            print(f"note: a {other} repo {rid} also exists — separate, not touched")

    exists = api.repo_exists(rid, repo_type="space")
    if exists:
        remote = set(api.list_repo_files(rid, repo_type="space"))
        clash = sorted(remote & set(SPACE_FILES))
        replacing += [f"file {f}" for f in clash]
        print(f"Space {rid} exists.")
        print(f"  files that would be replaced:  {clash or 'none'}")
        print(f"  files that would be added:     {sorted(set(SPACE_FILES) - remote) or 'none'}")
        print(f"  files left untouched:          {sorted(remote - set(SPACE_FILES)) or 'none'}")
        current_vars = {k: v.value for k, v in api.get_space_variables(rid).items()}
    else:
        print(f"Space {rid} does not exist — would create it "
              f"({'public' if args.public else 'private'}, Docker SDK) and add: {SPACE_FILES}")
        current_vars = {}

    if args.upstream:
        new = args.upstream.rstrip("/")
        old = current_vars.get("UPSTREAM_URL")
        if old and old != new:
            replacing.append(f"variable UPSTREAM_URL ({old} -> {new})")
        print(f"  variable UPSTREAM_URL: {old or '(unset)'} -> {new}")
    else:
        print(f"  variable UPSTREAM_URL: unchanged "
              f"({current_vars.get('UPSTREAM_URL') or 'unset — the Dockerfile default applies'})")
    if auth:
        # secret values can't be read back, so treat an existing Space as a replace
        if exists:
            replacing.append("secrets PROXY_USER/PROXY_PASSWORD (if already set)")
        print(f"  secrets PROXY_USER/PROXY_PASSWORD: would be set (user '{auth[0]}')")
    else:
        print("  secrets: unchanged")

    if args.dry_run:
        print("dry run — nothing changed")
        return
    if replacing and not args.yes:
        raise SystemExit("Refusing to replace existing items without --yes:\n  - "
                         + "\n  - ".join(replacing)
                         + "\n(old file versions stay in the Space's git history)")

    api.create_repo(rid, repo_type="space", space_sdk="docker",
                    private=not args.public, exist_ok=True)
    if args.upstream:
        api.add_space_variable(rid, "UPSTREAM_URL", args.upstream.rstrip("/"))
    if auth:
        api.add_space_secret(rid, "PROXY_USER", auth[0])
        api.add_space_secret(rid, "PROXY_PASSWORD", auth[1])
    api.upload_folder(folder_path=str(HERE), repo_id=rid, repo_type="space",
                      allow_patterns=SPACE_FILES, commit_message="Palette proxy Space")
    print(f"Space:  https://huggingface.co/spaces/{rid}")
    print(f"Direct: https://{rid.replace('/', '-').replace('_', '-').replace('.', '-').lower()}.hf.space")
    print("The Space rebuilds automatically (~1-2 min).")


if __name__ == "__main__":
    main()
