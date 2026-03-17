import os
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import app
from backup_utils import build_backup_zip, upload_backup_bytes_to_supabase
from security import log_audit
from models import db


def main():
    out_dir = Path(os.getenv("BACKUP_OUTPUT_DIR", "backups"))
    out_dir.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        bundle, filename, manifest = build_backup_zip(
            include_files=os.getenv("BACKUP_INCLUDE_FILES", "1") == "1"
        )

        bundle_bytes = bundle.getvalue()
        target = out_dir / filename
        target.write_bytes(bundle_bytes)

        print(f"Backup saved: {target}")
        print(
            f"Tables: {len(manifest.get('tables', {}))}, "
            f"files: {manifest.get('files_exported', 0)}"
        )

        cloud_enabled = os.getenv("BACKUP_UPLOAD_TO_SUPABASE", "1") == "1"

        if cloud_enabled:
            result = upload_backup_bytes_to_supabase(
                bundle_bytes=bundle_bytes,
                filename=filename,
                bucket=os.getenv("BACKUP_SUPABASE_BUCKET", "backups"),
                base_path=os.getenv("BACKUP_SUPABASE_PATH", "auto"),
                keep_last=int(os.getenv("BACKUP_KEEP_LAST", "30")),
            )
            print(f"Cloud backup uploaded: {result['bucket']}/{result['key']}")
            log_audit(
                "backup_cloud_upload",
                f"Автобэкап загружен в Supabase Storage: {result['bucket']}/{result['key']}",
            )
        else:
            print("Cloud backup upload skipped: BACKUP_UPLOAD_TO_SUPABASE=0")
            log_audit("backup_local_only", "Автобэкап сохранён только локально")

        db.session.commit()


if __name__ == "__main__":
    main()
