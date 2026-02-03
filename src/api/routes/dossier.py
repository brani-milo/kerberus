"""
Dossier Endpoints.

Provides document upload, storage, and search for user dossiers.
Each user has their own encrypted document storage.

SECURITY NOTE: Dossier operations require the user's password because
the encryption key is derived from it (zero-knowledge architecture).
"""
import logging
import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from pydantic import BaseModel, Field

from ..deps import get_current_user, get_db
from ...database.auth_db import AuthDB, verify_password
from ...search.dossier_search import DossierSearchService
from ...review.document_processor import DocumentProcessor
from ...security import get_pii_scrubber

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dossier", tags=["Dossier"])

# Document processor singleton
_doc_processor: Optional[DocumentProcessor] = None


def get_document_processor() -> DocumentProcessor:
    """Get document processor singleton."""
    global _doc_processor
    if _doc_processor is None:
        _doc_processor = DocumentProcessor()
    return _doc_processor


# ============================================
# Request/Response Models
# ============================================

class DossierAuthRequest(BaseModel):
    """Base request requiring dossier password."""
    password: str = Field(..., description="User password for dossier decryption")


class DocumentUploadResponse(BaseModel):
    """Response after document upload."""
    doc_id: str
    title: str
    doc_type: Optional[str]
    language: Optional[str]
    chunk_count: int
    content_length: int
    pii_scrubbed: bool = False
    pii_types: List[str] = []


class DocumentListItem(BaseModel):
    """Document summary for list view."""
    doc_id: str
    title: str
    doc_type: Optional[str]
    language: Optional[str]
    created_at: str
    updated_at: str
    content_length: int


class DocumentDetail(BaseModel):
    """Full document details."""
    doc_id: str
    title: str
    content: str
    doc_type: Optional[str]
    language: Optional[str]
    created_at: str
    updated_at: str
    metadata: Dict


class DossierSearchRequest(DossierAuthRequest):
    """Search request for dossier."""
    query: str = Field(..., min_length=2, max_length=500)
    limit: int = Field(10, ge=1, le=50)
    doc_type: Optional[str] = None
    multilingual: bool = False


class DossierSearchResult(BaseModel):
    """Single search result."""
    doc_id: str
    title: str
    doc_type: Optional[str]
    score: float
    text_preview: str
    chunk_index: int


class DossierSearchResponse(BaseModel):
    """Search response."""
    query: str
    results: List[DossierSearchResult]
    total_count: int


class DossierStatsResponse(BaseModel):
    """Dossier statistics."""
    document_count: int
    chunk_count: int
    vector_count: int
    file_size_mb: float
    collection_name: str


# ============================================
# Helper Functions
# ============================================

def verify_user_password(user: Dict, password: str, db: AuthDB) -> bool:
    """Verify user's password for dossier access."""
    full_user = db.get_user_by_id(str(user["user_id"]))
    if not full_user:
        return False
    return verify_password(password, full_user["password_hash"])


def get_dossier_service(user_id: str, password: str) -> DossierSearchService:
    """Create dossier service for user."""
    return DossierSearchService(
        user_id=user_id,
        user_password=password,
        is_firm=False,
        firm_id=None
    )


# ============================================
# Endpoints
# ============================================

@router.post(
    "/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Invalid file or password"},
        413: {"description": "File too large"},
    },
)
async def upload_document(
    file: UploadFile = File(...),
    password: str = Form(..., description="User password for dossier encryption"),
    title: Optional[str] = Form(None, description="Document title (defaults to filename)"),
    doc_type: Optional[str] = Form(None, description="Document type: contract, letter, brief, etc."),
    language: Optional[str] = Form(None, description="Language: de, fr, it, en"),
    scrub_pii: bool = Form(True, description="Scrub PII before storage"),
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Upload a document to the user's encrypted dossier.

    Supported formats: PDF, DOCX, TXT (max 50MB)

    The document is:
    1. Parsed to extract text
    2. Optionally scrubbed for PII
    3. Chunked for embedding
    4. Stored encrypted (SQLCipher)
    5. Embedded in Qdrant for search
    """
    user_id = str(user["user_id"])

    # Verify password
    if not verify_user_password(user, password, db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid password",
        )

    # Read file content
    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read file: {e}",
        )

    # Check file size (50MB limit)
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File too large. Maximum size is 50MB.",
        )

    # Parse document
    processor = get_document_processor()
    try:
        parsed = processor.parse_bytes(content, file.filename or "document")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Document parsing failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse document: {e}",
        )

    # Extract text
    text_content = parsed.full_text
    doc_title = title or parsed.filename

    # PII scrubbing
    pii_scrubbed = False
    pii_types = []

    if scrub_pii:
        scrubber = get_pii_scrubber()
        if scrubber.enabled:
            entities = scrubber.detect(text_content, language=language or "de")
            if entities:
                pii_scrubbed = True
                pii_types = list(set(e.entity_type for e in entities))
                text_content = scrubber.scrub(text_content, language=language or "de")
                logger.info(f"PII scrubbed from uploaded document: {pii_types}")

    # Store in dossier
    try:
        with get_dossier_service(user_id, password) as dossier:
            doc_id = dossier.add_document(
                title=doc_title,
                content=text_content,
                doc_type=doc_type,
                language=language,
                metadata={
                    "original_filename": file.filename,
                    "file_type": parsed.file_type,
                    "file_size": parsed.file_size,
                    "total_pages": parsed.total_pages,
                    "file_hash": parsed.file_hash,
                    "pii_scrubbed": pii_scrubbed,
                    "pii_types": pii_types,
                },
            )

            # Get chunk count
            stats = dossier.get_stats()
            chunk_count = stats.get("chunk_count", 0)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Dossier storage failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store document",
        )

    logger.info(f"Document uploaded: {doc_id} for user {user_id}")

    return DocumentUploadResponse(
        doc_id=doc_id,
        title=doc_title,
        doc_type=doc_type,
        language=language,
        chunk_count=chunk_count,
        content_length=len(text_content),
        pii_scrubbed=pii_scrubbed,
        pii_types=pii_types,
    )


@router.post("/documents/list", response_model=List[DocumentListItem])
async def list_documents(
    request: DossierAuthRequest,
    doc_type: Optional[str] = None,
    limit: int = 100,
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    List documents in the user's dossier.

    Requires password for dossier decryption.
    """
    user_id = str(user["user_id"])

    if not verify_user_password(user, request.password, db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid password",
        )

    try:
        with get_dossier_service(user_id, request.password) as dossier:
            docs = dossier.list_documents(doc_type=doc_type, limit=limit)

            return [
                DocumentListItem(
                    doc_id=doc["doc_id"],
                    title=doc["title"],
                    doc_type=doc.get("doc_type"),
                    language=doc.get("language"),
                    created_at=str(doc.get("created_at", "")),
                    updated_at=str(doc.get("updated_at", "")),
                    content_length=doc.get("content_length", 0),
                )
                for doc in docs
            ]

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/documents/{doc_id}", response_model=DocumentDetail)
async def get_document(
    doc_id: str,
    request: DossierAuthRequest,
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Get a specific document by ID.

    Requires password for dossier decryption.
    """
    user_id = str(user["user_id"])

    if not verify_user_password(user, request.password, db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid password",
        )

    try:
        with get_dossier_service(user_id, request.password) as dossier:
            doc = dossier.get_document(doc_id)

            if not doc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found",
                )

            return DocumentDetail(
                doc_id=doc["doc_id"],
                title=doc["title"],
                content=doc["content"],
                doc_type=doc.get("doc_type"),
                language=doc.get("language"),
                created_at=str(doc.get("created_at", "")),
                updated_at=str(doc.get("updated_at", "")),
                metadata=doc.get("metadata", {}),
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: str,
    request: DossierAuthRequest,
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Delete a document from the dossier.

    Removes both encrypted content and embeddings.
    """
    user_id = str(user["user_id"])

    if not verify_user_password(user, request.password, db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid password",
        )

    try:
        with get_dossier_service(user_id, request.password) as dossier:
            if not dossier.delete_document(doc_id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found",
                )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    logger.info(f"Document deleted: {doc_id} for user {user_id}")
    return None


@router.post("/search", response_model=DossierSearchResponse)
async def search_dossier(
    request: DossierSearchRequest,
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Search the user's dossier for relevant documents.

    Uses hybrid search (dense + sparse) by default.
    Set multilingual=true for cross-language search (dense only).
    """
    user_id = str(user["user_id"])

    if not verify_user_password(user, request.password, db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid password",
        )

    try:
        with get_dossier_service(user_id, request.password) as dossier:
            results = dossier.search(
                query=request.query,
                limit=request.limit,
                doc_type=request.doc_type,
                multilingual=request.multilingual,
            )

            return DossierSearchResponse(
                query=request.query,
                results=[
                    DossierSearchResult(
                        doc_id=r["doc_id"],
                        title=r["title"] or "Untitled",
                        doc_type=r.get("doc_type"),
                        score=r["score"],
                        text_preview=r.get("text_preview", "")[:300],
                        chunk_index=r.get("chunk_index", 0),
                    )
                    for r in results
                ],
                total_count=len(results),
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/stats", response_model=DossierStatsResponse)
async def get_dossier_stats(
    request: DossierAuthRequest,
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Get statistics about the user's dossier.
    """
    user_id = str(user["user_id"])

    if not verify_user_password(user, request.password, db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid password",
        )

    try:
        with get_dossier_service(user_id, request.password) as dossier:
            stats = dossier.get_stats()

            return DossierStatsResponse(
                document_count=stats.get("document_count", 0),
                chunk_count=stats.get("chunk_count", 0),
                vector_count=stats.get("vector_count", 0),
                file_size_mb=stats.get("file_size_mb", 0.0),
                collection_name=stats.get("collection_name", ""),
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
