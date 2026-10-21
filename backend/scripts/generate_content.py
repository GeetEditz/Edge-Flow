"""Generate the sample content catalogue:  python scripts/generate_content.py [target_dir]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.origin.content_gen import generate  # noqa: E402

target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "content"
print(f"wrote {generate(target)} files to {target}")
