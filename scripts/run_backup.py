import os
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import app
from backup_utils import (
    build_backup_excel_bytes,
    build_backup_zip,
    collect_backup_rows,
    upload_backup_bytes_to_supabase,
    upload_backup_bytes_to_s3,
)
from models import db
from security import log_audit


def main():
    out_dir = Path(os.getenv("BACKUP_OUTPUT_DIR", "backups"))
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = __import__('datetime').datetime.utcnow().strftime('%Y%m%d-%H%M%S')

    with app.app_context():
        data = collect_backup_rows()

        excel_enabled = os.getenv("BACKUP_CREATE_EXCEL", "1") == "1"
        excel_bytes = None
        excel_filename = None

        if excel_enabled:
            excel_bundle, excel_filename = build_backup_excel_bytes(data=data, timestamp=timestamp)
            excel_bytes = excel_bundle.getvalue()
            excel_target = out_dir / excel_filename
            excel_target.write_bytes(excel_bytes)
            print(f"Excel backup saved: {excel_target}")

        bundle, filename, manifest = build_backup_zip(
            include_files=os.getenv("BACKUP_INCLUDE_FILES", "1") == "1",
            timestamp=timestamp,
            include_excel=excel_enabled,
        )
        bundle_bytes = bundle.getvalue()
        target = out_dir / filename
        target.write_bytes(bundle_bytes)
        print(f"Backup saved: {target}")
        print(f"Tables: {len(manifest.get('tables', {}))}, files: {manifest.get('files_exported', 0)}")

        keep_last = int(os.getenv("BACKUP_KEEP_LAST", "10"))

        supabase_enabled = os.getenv("BACKUP_UPLOAD_TO_SUPABASE", "0") == "1"
        supabase_bucket = os.getenv("BACKUP_SUPABASE_BUCKET", "backups")
        supabase_path = os.getenv("BACKUP_SUPABASE_PATH", "auto")

        s3_enabled = os.getenv("BACKUP_UPLOAD_TO_S3", "0") == "1"
        s3_bucket = os.getenv("BACKUP_S3_BUCKET")
        s3_path = os.getenv("BACKUP_S3_PATH", "auto")

        uploaded_anywhere = False

        if supabase_enabled:
            try:
                zip_result = upload_backup_bytes_to_supabase(
                    bundle_bytes=bundle_bytes,
                    filename=filename,
                    bucket=supabase_bucket,
                    base_path=supabase_path,
                    keep_last=keep_last,
                    content_type='application/zip',
                )
                uploaded_anywhere = True
                print(f"Supabase ZIP backup uploaded: {zip_result['bucket']}/{zip_result['key']}")
                log_audit('backup_cloud_upload', f"Автобэкап ZIP загружен в Supabase Storage: {zip_result['bucket']}/{zip_result['key']}")

                if excel_enabled and excel_bytes and excel_filename:
                    excel_result = upload_backup_bytes_to_supabase(
                        bundle_bytes=excel_bytes,
                        filename=excel_filename,
                        bucket=supabase_bucket,
                        base_path=supabase_path,
                        keep_last=keep_last,
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    )
                    print(f"Supabase Excel backup uploaded: {excel_result['bucket']}/{excel_result['key']}")
                    log_audit('backup_cloud_excel_upload', f"Автобэкап Excel загружен в Supabase Storage: {excel_result['bucket']}/{excel_result['key']}")
            except Exception as exc:
                print(f"Supabase backup upload failed: {exc}")
                log_audit('backup_supabase_failed', f"Supabase backup upload failed: {str(exc)[:200]}")

        if s3_enabled:
            try:
                zip_result = upload_backup_bytes_to_s3(
                    bundle_bytes=bundle_bytes,
                    filename=filename,
                    bucket=s3_bucket,
                    base_path=s3_path,
                    keep_last=keep_last,
                    content_type='application/zip',
                )
                uploaded_anywhere = True
                print(f"S3 ZIP backup uploaded: {zip_result['bucket']}/{zip_result['key']}")
                log_audit('backup_s3_upload', f"Автобэкап ZIP загружен в S3/R2/Object Storage: {zip_result['bucket']}/{zip_result['key']}")

                if excel_enabled and excel_bytes and excel_filename:
                    excel_result = upload_backup_bytes_to_s3(
                        bundle_bytes=excel_bytes,
                        filename=excel_filename,
                        bucket=s3_bucket,
                        base_path=s3_path,
                        keep_last=keep_last,
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    )
                    print(f"S3 Excel backup uploaded: {excel_result['bucket']}/{excel_result['key']}")
                    log_audit('backup_s3_excel_upload', f"Автобэкап Excel загружен в S3/R2/Object Storage: {excel_result['bucket']}/{excel_result['key']}")
            except Exception as exc:
                print(f"S3 backup upload failed: {exc}")
                log_audit('backup_s3_failed', f"S3 backup upload failed: {str(exc)[:200]}")

        if not uploaded_anywhere:
            print("Cloud backup upload skipped or failed; backup saved locally only.")
            log_audit('backup_local_only', 'Автобэкап сохранён только локально')

        db.session.commit()


if __name__ == "__main__":
    main()
