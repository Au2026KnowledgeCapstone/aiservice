"""Backend/aiservice capability contracts, independent of execution jobs."""
from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator
Category = Literal["functional", "ux", "data", "performance", "security"]
Priority = Literal["critical", "high", "medium", "low"]

Text = Annotated[str, Field(min_length=1, max_length=2000)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class DocumentInput(Strict):
    id: str = Field(min_length=1, max_length=64)
    role: Literal["brd", "srs", "design_spec"]
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(max_length=150)
    content_base64: str = Field(min_length=1, max_length=14_000_000, repr=False)


class ExtractionRequest(Strict):
    request_id: str = Field(min_length=1, max_length=64)
    documents: list[DocumentInput] = Field(min_length=2, max_length=3)


class ExtractedRequirement(Strict):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(max_length=10000)
    category: Category = "functional"
    priority: Priority = "medium"
    criteria: list[Text] = Field(min_length=1, max_length=20)
    document_id: str
    excerpt: str = Field(min_length=1, max_length=10000)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    clarification_reason: str = Field(default="", max_length=2000)


class ExtractionResponse(Strict):
    request_id: str
    requirements: list[ExtractedRequirement] = Field(max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=100)


class VerificationRequest(Strict):
    request_id: str = Field(min_length=1, max_length=64)
    environment_id: str
    revision: int = Field(ge=1)
    target_url: str
    credentials: dict[str, str] = Field(default_factory=dict, repr=False)


class VerificationResponse(Strict):
    request_id: str
    environment_id: str
    revision: int
    status: Literal["verified", "rejected", "inconclusive"]
    reason: Literal["success_observed", "login_failed", "success_condition_missing", "target_blocked", "browser_error"]

    @model_validator(mode="after")
    def verified_requires_proof(self):
        if (self.status == "verified") != (self.reason == "success_observed"):
            raise ValueError("Verified status requires observed success")
        return self


class EvidenceResponse(Strict):
    id: str
    job_id: str
    type: Literal["screenshot", "page_text", "log"]
    media_type: str
    content_base64: str = Field(max_length=14_000_000, repr=False)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: str | None = None
