"""Check the Lisp wire encoder with Python's independent JSON decoder."""
import json
from pathlib import Path
import subprocess
import sys

codes = list(range(256)) + [0x7ff, 0x800, 0xd7ff, 0xe000, 0xffff, 0x10000, 0x1f642, 0x10ffff]
expected = ["", "a" * 100000, "".join(map(chr, codes))]
numbers = " ".join(map(str, codes))
expression = f"""(dolist (s (list "" (make-string 100000 :initial-element #\\a)
                              (map 'string #'code-char '({numbers}))))
                  (star-vector-wire:write-json-string s *standard-output*) (terpri))"""
result = subprocess.run([sys.argv[1] if len(sys.argv) > 1 else "sbcl", "--noinform",
    "--non-interactive", "--no-userinit", "--no-sysinit", "--load",
    str(Path(__file__).resolve().parents[1] / "lisp/json-stream.lisp"),
    "--eval", expression], capture_output=True, text=True, check=True, timeout=30)
assert [json.loads(line) for line in result.stdout.splitlines()] == expected
print("PASS: streaming JSON controls, Unicode boundaries, supplementary characters and large strings")
