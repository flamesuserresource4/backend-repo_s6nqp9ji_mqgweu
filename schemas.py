"""
Database Schemas for Multi-Tenant Survey Platform

Each Pydantic model represents a MongoDB collection. The collection
name is the lowercase of the class name (handled by the helper layer).

This schema file is used by the in-environment DB viewer and also as
imported models for request validation in the API.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# ---------- Core Multi-tenant ----------
class Organization(BaseModel):
    name: str = Field(..., description="Organization name")
    plan: str = Field("trial", description="Plan tier: trial|basic|pro|enterprise")
    settings: Dict[str, Any] = Field(default_factory=dict)

class Profile(BaseModel):
    org_id: str = Field(..., description="Organization ID")
    email: str
    name: str
    avatar_url: Optional[str] = None
    roles: List[str] = Field(default_factory=list, description="Roles: super_admin|org_admin|premium|standard")

# ---------- Datasets ----------
class DatasetVersion(BaseModel):
    version: int
    filename: str
    rows: int
    columns: List[str]
    distincts: Dict[str, List[str]] = Field(default_factory=dict)
    created_by: Optional[str] = None

class Dataset(BaseModel):
    org_id: str
    name: str
    description: Optional[str] = None
    current_version: int = 1
    versions: List[DatasetVersion] = Field(default_factory=list)
    history_notes: List[str] = Field(default_factory=list)

# Rows are stored in a separate collection (datasetrow) with dataset_id and version
class DatasetRow(BaseModel):
    dataset_id: str
    version: int
    data: Dict[str, Any]

# ---------- Surveys ----------
class CascadeConfig(BaseModel):
    unique_key: Optional[str] = None
    label_column: Optional[str] = None
    cascade_levels: List[str] = Field(default_factory=list)
    searchable_columns: List[str] = Field(default_factory=list)
    hidden_columns: List[str] = Field(default_factory=list)
    allow_overwrite: bool = False

class Question(BaseModel):
    id: str
    type: str  # short_text, long_text, number, choice, dropdown_dataset, searchable_dropdown, photo
    text: str
    help_text: Optional[str] = None
    required: bool = False
    export_header: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None  # {questionId, op, value}
    settings: Dict[str, Any] = Field(default_factory=dict)

class SurveySettings(BaseModel):
    response_style: str = Field("one_by_one", description="one_by_one|all_at_once|card_shuffle")
    anonymous: bool = True
    require_auth: bool = False
    response_limit_per_user: Optional[int] = None
    target_response_count: Optional[int] = None
    duration: str = Field("ongoing", description="1w|2w|1m|3m|ongoing")
    primary_language: str = "en"
    theme_color: str = "#2563eb"

class Survey(BaseModel):
    org_id: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    client_name: Optional[str] = None
    type: str = Field("simple", description="simple|dataset")
    dataset_id: Optional[str] = None
    cascade: Optional[CascadeConfig] = None
    questions: List[Question] = Field(default_factory=list)
    settings: SurveySettings = Field(default_factory=SurveySettings)
    status: str = Field("draft", description="draft|active|closed")
    created_by: Optional[str] = None

class SurveyResponse(BaseModel):
    survey_id: str
    org_id: str
    answers: List[Dict[str, Any]] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)  # device, geo, photos metadata, etc.
    submitted_by: Optional[str] = None  # profile id or email
    anonymous: bool = False

# ---------- Admin/Audit ----------
class AuditLog(BaseModel):
    org_id: str
    actor: Optional[str] = None
    action: str
    resource: str
    data: Dict[str, Any] = Field(default_factory=dict)
"""
Notes:
- For simplicity we keep photo storage as URLs. A future iteration can integrate object storage.
- Dataset rows are stored in datasetrow collection per version.
"""
