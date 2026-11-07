"""Serialisation helpers for the document endpoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.ingestion.types import DocumentInfo, IndexReport, IngestResult, ReindexSummary, VersionInfo


def version_to_dict(version: VersionInfo) -> dict[str, Any]:
    return asdict(version)


def document_to_dict(document: DocumentInfo) -> dict[str, Any]:
    return asdict(document)


def ingest_result_to_dict(result: IngestResult) -> dict[str, Any]:
    return {
        "outcome": result.outcome,
        "document": document_to_dict(result.document),
        "version": version_to_dict(result.version),
    }


def reindex_summary_to_dict(summary: ReindexSummary) -> dict[str, Any]:
    return {"total": summary.total, "succeeded": summary.succeeded, "failed": summary.failed}


def index_report_to_dict(report: IndexReport) -> dict[str, Any]:
    return asdict(report)
