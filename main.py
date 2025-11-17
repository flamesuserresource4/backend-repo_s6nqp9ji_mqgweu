import os
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from io import StringIO
import csv
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import Organization, Profile, Dataset, DatasetVersion, DatasetRow, Survey, SurveyResponse, AuditLog

app = FastAPI(title="Multi-Tenant Survey Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Utilities ----------
class IdModel(BaseModel):
    id: str


def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")


def safe_obj(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    doc["id"] = str(doc.get("_id"))
    doc.pop("_id", None)
    return doc


# ---------- Health ----------
@app.get("/")
def read_root():
    return {"message": "Survey Platform Backend Running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    return response


# ---------- Organizations & Profiles ----------
@app.post("/api/orgs")
def create_org(payload: Organization):
    org_id = create_document("organization", payload)
    return {"id": org_id}


@app.get("/api/orgs")
def list_orgs():
    docs = get_documents("organization")
    return [safe_obj(d) for d in docs]


@app.post("/api/profiles")
def create_profile(payload: Profile):
    prof_id = create_document("profile", payload)
    return {"id": prof_id}


# ---------- Datasets ----------
@app.post("/api/datasets/upload")
async def upload_dataset(
    org_id: str = Form(...),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
):
    # Only CSV supported in this MVP
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV supported in this version")

    content = (await file.read()).decode("utf-8", errors="ignore")
    reader = csv.DictReader(StringIO(content))
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="No rows found in CSV")

    columns = list(rows[0].keys())
    distincts: Dict[str, List[str]] = {c: [] for c in columns}
    for r in rows:
        for c in columns:
            v = r.get(c)
            if v is not None and v not in distincts[c]:
                distincts[c].append(v)

    version = DatasetVersion(
        version=1,
        filename=file.filename,
        rows=len(rows),
        columns=columns,
        distincts=distincts,
    )
    dataset = Dataset(
        org_id=org_id,
        name=name,
        description=description,
        current_version=1,
        versions=[version],
    )
    dataset_id = create_document("dataset", dataset)

    # Store rows
    for r in rows:
        create_document("datasetrow", DatasetRow(dataset_id=dataset_id, version=1, data=r))

    return {"id": dataset_id, "columns": columns, "rows": len(rows), "distincts": distincts}


@app.get("/api/datasets")
def list_datasets(org_id: Optional[str] = None):
    filt = {"org_id": org_id} if org_id else {}
    docs = get_documents("dataset", filt)
    return [safe_obj(d) for d in docs]


@app.get("/api/datasets/{dataset_id}")
def get_dataset(dataset_id: str):
    doc = db["dataset"].find_one({"_id": oid(dataset_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return safe_obj(doc)


# ---------- Surveys ----------
@app.post("/api/surveys")
def create_survey(payload: Survey):
    survey_id = create_document("survey", payload)
    return {"id": survey_id}


@app.get("/api/surveys")
def list_surveys(org_id: Optional[str] = None):
    filt = {"org_id": org_id} if org_id else {}
    docs = get_documents("survey", filt)
    return [safe_obj(d) for d in docs]


@app.get("/api/surveys/{survey_id}")
def get_survey(survey_id: str):
    doc = db["survey"].find_one({"_id": oid(survey_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Survey not found")
    return safe_obj(doc)


class PublishModel(BaseModel):
    status: str  # draft|active|closed


@app.post("/api/surveys/{survey_id}/status")
def set_survey_status(survey_id: str, payload: PublishModel):
    if payload.status not in ["draft", "active", "closed"]:
        raise HTTPException(status_code=400, detail="Invalid status")
    res = db["survey"].update_one({"_id": oid(survey_id)}, {"$set": {"status": payload.status}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Survey not found")
    return {"id": survey_id, "status": payload.status}


# ---------- Responses ----------
@app.post("/api/surveys/{survey_id}/responses")
def submit_response(survey_id: str, payload: SurveyResponse):
    # Basic enforcement: if survey requires auth and not provided, reject (MVP sim)
    survey = db["survey"].find_one({"_id": oid(survey_id)})
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")

    settings = survey.get("settings", {})
    require_auth = settings.get("require_auth", False)
    if require_auth and not payload.submitted_by and not payload.anonymous:
        raise HTTPException(status_code=403, detail="Authentication required")

    # Enforce response limit per user if provided
    limit = settings.get("response_limit_per_user")
    if limit and payload.submitted_by:
        count = db["surveyresponse"].count_documents({
            "survey_id": survey_id,
            "submitted_by": payload.submitted_by
        })
        if count >= int(limit):
            raise HTTPException(status_code=429, detail="Response limit reached")

    resp_id = create_document("surveyresponse", payload)
    return {"id": resp_id}


@app.get("/api/surveys/{survey_id}/responses")
def list_responses(survey_id: str):
    docs = get_documents("surveyresponse", {"survey_id": survey_id})
    return [safe_obj(d) for d in docs]


@app.get("/api/surveys/{survey_id}/export/csv")
def export_csv(survey_id: str):
    survey = db["survey"].find_one({"_id": oid(survey_id)})
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")

    responses = list(db["surveyresponse"].find({"survey_id": survey_id}))

    # Build dynamic headers from survey questions export_header or text
    questions = survey.get("questions", [])
    headers = []
    for q in questions:
        headers.append(q.get("export_header") or q.get("text") or q.get("id"))
    # Add meta fields
    headers.extend(["submitted_by", "anonymous", "created_at"])  # created_at comes from helper

    def iter_csv():
        sio = StringIO()
        writer = csv.writer(sio)
        writer.writerow(headers)
        yield sio.getvalue()
        sio.seek(0)
        sio.truncate(0)
        for r in responses:
            ans_map = {a.get("questionId") or a.get("id") or a.get("text"): a.get("value") for a in r.get("answers", [])}
            row = []
            for q in questions:
                # Try map by id first then text
                val = ans_map.get(q.get("id"))
                if val is None:
                    val = ans_map.get(q.get("text"))
                row.append(val if val is not None else "")
            row.extend([
                r.get("submitted_by", ""),
                r.get("anonymous", False),
                str(r.get("created_at", "")),
            ])
            writer.writerow(row)
            yield sio.getvalue()
            sio.seek(0)
            sio.truncate(0)

    return StreamingResponse(iter_csv(), media_type="text/csv", headers={
        "Content-Disposition": f"attachment; filename=survey_{survey_id}_responses.csv"
    })


# ---------- Schemas endpoint for viewer tools ----------
@app.get("/schema")
def get_schema_definitions():
    # Return class names for collections - light introspection
    return {
        "collections": [
            "organization", "profile", "dataset", "datasetrow",
            "survey", "surveyresponse", "auditlog"
        ]
    }


# ---------- Simple logging ----------
@app.post("/api/audit")
def log_action(payload: AuditLog):
    log_id = create_document("auditlog", payload)
    return {"id": log_id}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
