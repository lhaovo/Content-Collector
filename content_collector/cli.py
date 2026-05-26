from __future__ import annotations

from pathlib import Path

import typer
import uvicorn

from content_collector.database import init_db, new_session
from content_collector.social_xlsx import download_account_assets, import_social_xlsx
from content_collector.workflows import ImportWorkflow

app = typer.Typer(no_args_is_help=True)


@app.command("init-db")
def init_database() -> None:
    init_db()
    typer.echo("数据库已初始化")


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8632, reload: bool = False) -> None:
    uvicorn.run("content_collector.main:app", host=host, port=port, reload=reload)


@app.command("ingest")
def ingest(root_path: Path, auto_extract: bool = False) -> None:
    with new_session() as session:
        scan = ImportWorkflow(session).run(str(root_path), auto_extract=auto_extract)
        typer.echo(f"扫描完成：{scan.id}，文件数：{scan.file_count}")


@app.command("import-social-xlsx")
def import_social_xlsx_command(xlsx_path: Path, platform: str = "xhs") -> None:
    with new_session() as session:
        result = import_social_xlsx(session, xlsx_path, platform=platform)
        typer.echo(
            "导入完成："
            f"行数 {result.total_rows}，"
            f"新增内容 {result.created_posts}，"
            f"更新内容 {result.updated_posts}，"
            f"新增素材 {result.created_assets}，"
            f"待下载素材 {result.remote_assets}"
        )


@app.command("download-social-assets")
def download_social_assets_command(platform: str, author_id: str, delay_seconds: float = 2.0) -> None:
    with new_session() as session:
        result = download_account_assets(session, platform, author_id, delay_seconds=delay_seconds)
        typer.echo(
            "下载完成："
            f"总素材 {result.total_assets}，"
            f"下载 {result.downloaded}，"
            f"跳过 {result.skipped}，"
            f"失败 {result.failed}"
        )


@app.command("extract-scan")
def extract_scan(scan_id: str) -> None:
    with new_session() as session:
        ImportWorkflow(session).extract_scan(scan_id)
        typer.echo("抽取完成")


@app.command("extract-post")
def extract_post(post_id: str) -> None:
    with new_session() as session:
        ImportWorkflow(session).extract_post(post_id)
        typer.echo("抽取完成")


if __name__ == "__main__":
    app()
