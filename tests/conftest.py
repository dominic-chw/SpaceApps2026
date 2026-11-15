"""Tests never depend on downloaded data/: every source variable is forced to synthetic
(pipeline/common/sources.py honours SPACEAPPS_SOURCES=synthetic). Source-loader tests that need a
specific config pass `sources_force: True` in their params."""
import os

os.environ["SPACEAPPS_SOURCES"] = "synthetic"
