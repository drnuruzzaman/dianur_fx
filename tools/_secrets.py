"""
API keys, read from configs/secrets.env.

WHY A FILE AND NOT A CONSTANT. A key pasted into a script is a key in every
diff, every backup and every screenshot of that file. This keeps them in one
place, that place is gitignored, and the loader below never prints a value --
only whether it found one.

FORMAT is dotenv, deliberately the dullest possible:

    FRED_API_KEY=abc123
    FINNHUB_API_KEY=def456     # trailing comments are stripped

THE ENVIRONMENT WINS. A key already exported in the shell is left alone, so a
one-off `set FRED_API_KEY=...` still overrides the file without editing it.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(ROOT, 'configs', 'secrets.env')


def load(path=SECRETS):
    """Merge the file into os.environ. Returns the names it set."""
    if not os.path.exists(path):
        return []
    added = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.split(' #')[0].strip().strip('"').strip("'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v
                added.append(k)
    return added


def get(name, path=SECRETS):
    """The value for `name`, from the environment or the file. None if absent."""
    if os.environ.get(name):
        return os.environ[name]
    load(path)
    return os.environ.get(name)
