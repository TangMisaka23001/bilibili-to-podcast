#!/bin/bash
set -e
b2p-prune && b2p-fetch && b2p-rss && b2p-sync
