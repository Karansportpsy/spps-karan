"""Stage 13C — clinical participation: programmes, tasks, self-report submissions,
practitioner review and explicitly released summaries.

Practitioner routes run clinical `athlete_for` (FORCE RLS) before touching the
platform database. Portal routes derive the actor from the portal session. Nothing
here reads clinical notes, reports or assessment narratives.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .portal_auth import sha256_hex

ERRORS = {"28000": (401, "Session expired"), "42501": (403, "Not permitted"), "P0002": (404, "Not found"),
          "P0003": (409, "Record consent to take part in this programme first"),
          "P0004": (409, "This action is not available in the current state"),
          "P0006": (409, "This task was changed by someone else; reload and try again"),
          "22023": (422, "Invalid details")}


class ProgrammeIn(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    goal: str = Field(default="", max_length=1000)


class TaskIn(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    instructions: str = Field(default="", max_length=2000)
    frequency: Literal["once", "daily", "weekly", "as_needed"] = "weekly"
    due_date: datetime | None = None


class TaskUpdateIn(TaskIn):
    expected_revision: int = Field(ge=1)


class TransitionIn(BaseModel):
    status: Literal["ASSIGNED", "PAUSED", "ACCEPTED", "COMPLETED", "DISCONTINUED"]


class ReviewIn(BaseModel):
    feedback: str = Field(min_length=2, max_length=1000)


class SummaryIn(BaseModel):
    kind: Literal["assessment_summary", "progress_summary", "session_summary"]
    title: str = Field(min_length=3, max_length=140)
    body: str = Field(min_length=5, max_length=2000)


class RespondIn(BaseModel):
    accept: bool
    reason: str = Field(default="", max_length=240)


class SubmitIn(BaseModel):
    completed: bool
    self_rating: int | None = Field(default=None, ge=0, le=10)
    reflection: str = Field(default="", max_length=1000)


def register_programme_routes(app: FastAPI, db_session, get_current, athlete_for, event) -> None:
    engine = app.state.portal_auth_engine
    current_session = app.state.portal_current_session

    def call(name, **params):
        placeholders = ",".join(f":{k}" for k in params)
        try:
            with engine.begin() as conn:
                return conn.execute(text(f"SELECT spps_identity_dev.{name}({placeholders})"), params).scalar()
        except DBAPIError as exc:
            code = getattr(getattr(exc, "orig", None), "sqlstate", "")
            if code in ERRORS:
                raise HTTPException(*ERRORS[code]) from None
            raise

    def audit(db, user, action, typ, oid):
        event(db, user, action, typ, oid)
        db.commit()

    # ------------------------------------------------------------ practitioner
    @app.get("/api/athletes/{athlete_id}/participation")
    def participation(athlete_id: int, user=Depends(get_current), db=Depends(db_session)):
        athlete = athlete_for(db, user, athlete_id)
        return call("practitioner_participation", p_practitioner_id=user.id, p_athlete_record_id=athlete.id)

    @app.post("/api/athletes/{athlete_id}/programmes", status_code=201)
    def create_programme(athlete_id: int, payload: ProgrammeIn, user=Depends(get_current), db=Depends(db_session)):
        athlete = athlete_for(db, user, athlete_id)
        pid = call("practitioner_create_programme", p_practitioner_id=user.id, p_athlete_record_id=athlete.id,
                   p_title=payload.title, p_goal=payload.goal)
        audit(db, user, "programme.created", "platform_programme", pid)
        return {"programme_id": pid, "status": "DRAFT"}

    @app.post("/api/programmes/{programme_id}/tasks", status_code=201)
    def add_task(programme_id: int, payload: TaskIn, user=Depends(get_current)):
        return {"task_id": call("practitioner_add_task", p_practitioner_id=user.id, p_programme_id=programme_id,
                                p_title=payload.title, p_instructions=payload.instructions,
                                p_frequency=payload.frequency, p_due=payload.due_date)}

    @app.post("/api/programme-tasks/{task_id}/update")
    def update_task(task_id: int, payload: TaskUpdateIn, user=Depends(get_current)):
        return {"revision": call("practitioner_update_task", p_practitioner_id=user.id, p_task_id=task_id,
                                 p_title=payload.title, p_instructions=payload.instructions,
                                 p_frequency=payload.frequency, p_due=payload.due_date,
                                 p_expected_revision=payload.expected_revision)}

    @app.post("/api/programme-tasks/{task_id}/discontinue")
    def discontinue_task(task_id: int, user=Depends(get_current)):
        call("practitioner_discontinue_task", p_practitioner_id=user.id, p_task_id=task_id)
        return {"status": "DISCONTINUED"}

    @app.post("/api/programmes/{programme_id}/status")
    def transition(programme_id: int, payload: TransitionIn, user=Depends(get_current), db=Depends(db_session)):
        status = call("practitioner_programme_transition", p_practitioner_id=user.id, p_programme_id=programme_id,
                      p_to=payload.status)
        audit(db, user, f"programme.{status.lower()}", "platform_programme", programme_id)
        return {"programme_id": programme_id, "status": status}

    @app.post("/api/task-submissions/{submission_id}/review", status_code=201)
    def review(submission_id: int, payload: ReviewIn, user=Depends(get_current)):
        return {"review_id": call("practitioner_review_submission", p_practitioner_id=user.id,
                                  p_submission_id=submission_id, p_feedback=payload.feedback)}

    @app.post("/api/athletes/{athlete_id}/released-summaries", status_code=201)
    def release(athlete_id: int, payload: SummaryIn, user=Depends(get_current), db=Depends(db_session)):
        athlete = athlete_for(db, user, athlete_id)
        sid = call("practitioner_release_summary", p_practitioner_id=user.id, p_athlete_record_id=athlete.id,
                   p_kind=payload.kind, p_title=payload.title, p_body=payload.body)
        audit(db, user, "summary.released", "platform_summary", sid)
        return {"summary_id": sid}

    @app.post("/api/released-summaries/{summary_id}/withdraw")
    def withdraw(summary_id: int, user=Depends(get_current), db=Depends(db_session)):
        call("practitioner_withdraw_summary", p_practitioner_id=user.id, p_summary_id=summary_id)
        audit(db, user, "summary.withdrawn", "platform_summary", summary_id)
        return {"withdrawn": True}

    # ------------------------------------------------------------ portal
    @app.get("/api/portal/programmes")
    def portal_programmes(session=Depends(current_session)):
        return call("portal_participation", p_token_hash=sha256_hex(session["token"]))

    @app.post("/api/portal/programmes/{programme_id}/respond")
    def portal_respond(programme_id: int, payload: RespondIn, session=Depends(current_session)):
        return {"programme_id": programme_id,
                "status": call("portal_respond_programme", p_token_hash=sha256_hex(session["token"]),
                               p_programme_id=programme_id, p_accept=payload.accept, p_reason=payload.reason)}

    @app.post("/api/portal/tasks/{task_id}/submit", status_code=201)
    def portal_submit(task_id: int, payload: SubmitIn, session=Depends(current_session)):
        return {"submission_id": call("portal_submit_task", p_token_hash=sha256_hex(session["token"]),
                                      p_task_id=task_id, p_completed=payload.completed,
                                      p_rating=payload.self_rating, p_reflection=payload.reflection)}
