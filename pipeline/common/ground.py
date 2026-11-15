"""Shared ground rules: hardness from ground class and rock (plan s6). Config-driven."""
from .config import get_processing


def hardness(ground_class, rock, params=None):
    r = (params or get_processing())["ground_rules"]
    if ground_class == "bedrock":
        return r["bedrock_hardness"].get(rock, r["hardness"]["bedrock"])
    return r["hardness"][ground_class]
