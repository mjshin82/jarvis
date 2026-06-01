#!/usr/bin/env bash
set -e
swiftc audiod.swift -o audiod -framework AVFoundation
echo "built ./audiod"
