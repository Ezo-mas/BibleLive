"""Audio pipeline health reports and operator status."""

import time
from typing import Literal, Protocol

from .models import PipelineHeartbeat, PipelineStatusResponse
from .service_log import ServiceLog

# How many report intervals the audio pipeline may miss before the operator is
# told it is offline. One late report (a slow request, a GC pause) is not news.
PIPELINE_MISSED_REPORTS_OFFLINE = 3


class PipelineHealthOwner(Protocol):
    service_log: ServiceLog


class PipelineHealth:
    """Own health state; shared dependencies are looked up on the owner at call time."""

    def __init__(self, owner: PipelineHealthOwner) -> None:
        self._owner = owner
        # The audio pipeline's latest health report and when it arrived
        # (time.monotonic()), or None until the first one.
        self.pipeline_report: PipelineHeartbeat | None = None
        self.pipeline_reported_at: float | None = None
        self._logged_pipeline_status: tuple[str, frozenset[str]] | None = None

    def record_pipeline_report(self, report: PipelineHeartbeat, now: float | None = None) -> None:
        """Keep the audio pipeline's latest health report."""
        self.pipeline_report = report
        self.pipeline_reported_at = time.monotonic() if now is None else now
        self.pipeline_status(now)

    def pipeline_status(self, now: float | None = None) -> PipelineStatusResponse:
        """Summarize the pipeline's health for the operator console.

        The pipeline cannot report that it has died, so silence is the signal:
        once more than ``PIPELINE_MISSED_REPORTS_OFFLINE`` of its own report
        intervals pass without a report, it is offline, whatever it said last.
        """
        report, reported_at = self.pipeline_report, self.pipeline_reported_at
        if report is None or reported_at is None:
            self._log_pipeline_status("unknown", [])
            return PipelineStatusResponse(status="unknown")

        age = max(0.0, (time.monotonic() if now is None else now) - reported_at)
        status: Literal["ok", "degraded", "offline"]
        if age > report.interval_s * PIPELINE_MISSED_REPORTS_OFFLINE:
            status = "offline"
        elif report.problems:
            status = "degraded"
        else:
            status = "ok"
        self._log_pipeline_status(
            status, [] if status == "offline" else [i.code for i in report.issues]
        )
        return PipelineStatusResponse(
            status=status,
            language=report.language,
            # Problems from a pipeline that has gone quiet are history, not state.
            problems=[] if status == "offline" else list(report.problems),
            issues=[] if status == "offline" else list(report.issues),
            seconds_since_report=round(age, 1),
        )

    def _log_pipeline_status(self, status: str, issue_codes: list[str]) -> None:
        key = (status, frozenset(issue_codes) if status == "degraded" else frozenset())
        if key != self._logged_pipeline_status:
            self._logged_pipeline_status = key
            self._owner.service_log.emit("pipeline_status", status=status, issue_codes=issue_codes)
