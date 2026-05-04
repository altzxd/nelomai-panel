from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import SessionLocal
from app.services import (
    announce_initial_admin_setup_if_needed,
    normalize_stored_ssh_secrets,
    run_scheduled_backup_if_due,
    scrub_sensitive_audit_logs,
    write_audit_log,
)
from app.web import audit_http_exception_handler, router as web_router


def create_app() -> FastAPI:
    application = FastAPI(title=settings.app_name, debug=settings.debug)
    application.add_exception_handler(HTTPException, audit_http_exception_handler)
    application.include_router(web_router)
    application.mount("/static", StaticFiles(directory="app/static"), name="static")

    @application.on_event("startup")
    def run_scheduled_backup_startup() -> None:
        with SessionLocal() as db:
            normalize_stored_ssh_secrets(db)
            scrub_sensitive_audit_logs(db)
            setup_url = announce_initial_admin_setup_if_needed(db, settings.panel_public_base_url)
            if setup_url:
                print(f"Initial admin setup link: {setup_url}")
            try:
                run_scheduled_backup_if_due(db)
            except Exception as exc:
                write_audit_log(
                    db,
                    event_type="backups.scheduled_failed",
                    severity="error",
                    message=f"Scheduled backup failed: {exc}",
                    message_ru=f"Не удалось выполнить бэкап по расписанию: {exc}",
                    details=str(exc),
                )

    return application


app = create_app()
