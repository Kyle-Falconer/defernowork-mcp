#!/bin/sh
# capture-url.sh — Claude Code's BROWSER hook. Writes the URL it would
# have opened into a FIFO that the test fixture reads.
echo "$1" > /tmp/auth-url.fifo
exit 0
