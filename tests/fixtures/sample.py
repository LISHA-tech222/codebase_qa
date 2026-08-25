"""A tiny sample module to test our AST chunker."""

import os
import json

MAX_RETRIES = 3

class Config:
    """Holds app configuration."""

    def __init__(self, path):
        self.path = path

    def load(self):
        """Load config from disk as JSON."""
        with open(self.path) as f:
            return json.load(f)

    def save(self, data):
        with open(self.path, "w") as f:
            json.dump(data, f)


def retry(fn, attempts=MAX_RETRIES):
    """Retry a function up to `attempts` times."""
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            if i == attempts - 1:
                raise

def main():
    cfg = Config("config.json")
    print(cfg.load())
