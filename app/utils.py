import os
import re

def extract_number(dirname):
    match = re.search(r'\d+', dirname)
    return int(match.group()) if match else 0

def safe_path(base, relative):
    full = os.path.join(base, relative)
    real_full = os.path.realpath(full)
    real_base = os.path.realpath(base)
    if not real_full.startswith(real_base):
        raise ValueError("Invalid path")
    return real_full
