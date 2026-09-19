// A tiny sample module to test our JS chunker.
import fs from "fs";

const MAX_RETRIES = 3;

class Config {
  constructor(path) {
    this.path = path;
  }

  /** Load config from disk as JSON. */
  load() {
    return JSON.parse(fs.readFileSync(this.path, "utf-8"));
  }

  save(data) {
    fs.writeFileSync(this.path, JSON.stringify(data));
  }
}

/**
 * Retry a function up to `attempts` times.
 */
function retry(fn, attempts = MAX_RETRIES) {
  for (let i = 0; i < attempts; i++) {
    try {
      return fn();
    } catch (e) {
      if (i === attempts - 1) throw e;
    }
  }
}

const main = () => {
  const cfg = new Config("config.json");
  console.log(cfg.load());
};

export function notImplementedYet() {
  throw new Error("not implemented");
}
