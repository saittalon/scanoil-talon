import os
from pathlib import Path

from app import app
from backup_utils import build_backup_zip

out_dir = Path(os.getenv("BACKUP_OUTPUT_DIR", "backups"))
out_dir.mkdir(parents=True, exist_ok=True)

with app.app_context():
    bundle, filename, manifest = build_backup_zip(include_files=os.getenv("BACKUP_INCLUDE_FILES", "1") == "1")
    target = out_dir / filename
    target.write_bytes(bundle.getvalue())
    print(f"Backup saved: {target}")
    print(f"Tables: {len(manifest.get('tables', {}))}, files: {manifest.get('files_exported', 0)}")
