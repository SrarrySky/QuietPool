# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-21

### Added

- Historical replay harness for the EU market-manipulation indicators
  MAR (a)-(e).
- Fixed-input counterfactual simulation from historical PancakeSwap V2 swaps.
- Protocol freeze and verification with SHA-256 and git-blob fingerprinting.
- `redteam` command-line interface: `demo`, `plan`, `freeze`, `run`, and `plot`.
- Pinax BSC Parquet downloader and importer with exact reserve accounting.
- Fifteen configurations spanning M0-M7 plus a seven-scenario sensitivity
  suite.
- Figure and reporting pipeline with provenance manifests.
- Resource budget admission (memory, wall time, disk, and indicator-row limits).
- Deterministic seed-42 execution with parallel worker support.
