"""Durable private portrait storage using the existing ownership checks and Git CAS."""
import argparse
import json
import os

from zotwatch.center_profiles import NAME, MAX_BYTES, ProfileCache
from zotwatch.metadata_transport import MetadataGitState


class ProfileGitState(MetadataGitState):
    branch = 'zotwatch-center-profiles-v1'
    marker = 'center-profiles-remote-v1.json'
    name = NAME
    max_bytes = MAX_BYTES
    validate_cache = staticmethod(ProfileCache.validate)
    commit_message = 'Private interest center portraits v1'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['restore', 'publish'])
    parser.add_argument('--state', required=True)
    args = parser.parse_args()
    remote = ProfileGitState(os.environ['GITHUB_REPOSITORY'], int(os.environ['GITHUB_REPOSITORY_ID']), os.environ['GITHUB_TOKEN'])
    result = remote.restore(args.state) if args.command == 'restore' else remote.publish(args.state)
    print(json.dumps({'result': result}))


if __name__ == '__main__':
    main()
